"""Holt Tageswerte und Aktivitäten von Garmin Connect und legt sie lokal ab."""

import io
import logging
import time
import zipfile
from datetime import date, timedelta
from pathlib import Path

from garminconnect import Garmin

from db import Store

log = logging.getLogger("garmin_coach.sync")

# Tageswerte, die fürs Coaching relevant sind. Intraday-Kurven (Herzfrequenz,
# Stress im 5-Minuten-Takt) lassen wir bewusst weg.
DAILY_SOURCES = {
    "user_summary": lambda g, d: g.get_user_summary(d),        # Ruhepuls, Stress-Schnitt, Body Battery, Schritte
    "sleep": lambda g, d: g.get_sleep_data(d),                 # Dauer, Phasen, Schlafwert, Atemfrequenz
    "hrv": lambda g, d: g.get_hrv_data(d),                     # Nacht-HRV, Status, Basiswert
    "training_readiness": lambda g, d: g.get_training_readiness(d),
    "training_status": lambda g, d: g.get_training_status(d),  # akute/chronische Belastung, Status
    "max_metrics": lambda g, d: g.get_max_metrics(d),          # VO2max
}

# Die letzten Tage werden immer neu geholt, weil Garmin sie nachträglich ergänzt
# (z. B. Schlaf und HRV erst nach dem Aufwachen, Belastung nach dem Training).
REFRESH_RECENT_DAYS = 3

PAUSE_S = 0.6  # kleine Pause zwischen Abrufen, um Garmin nicht zu fluten


def _days(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def _get(data, *path):
    """Sicher in verschachtelte Garmin-Antworten greifen."""
    for key in path:
        if isinstance(data, list):
            data = data[0] if data else None
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def _day_summary(values: dict) -> str:
    """Kurzer, lesbarer Überblick über einen Tag fürs Protokoll."""
    parts = []
    hrv = _get(values.get("hrv"), "hrvSummary", "lastNightAvg")
    if hrv:
        status = _get(values.get("hrv"), "hrvSummary", "status") or ""
        parts.append(f"HRV {hrv} ms {status}".strip())
    rhr = _get(values.get("user_summary"), "restingHeartRate")
    if rhr:
        parts.append(f"Ruhepuls {rhr}")
    sleep_s = _get(values.get("sleep"), "dailySleepDTO", "sleepTimeSeconds")
    if sleep_s:
        parts.append(f"Schlaf {int(sleep_s) // 3600}:{int(sleep_s) % 3600 // 60:02d} h")
    bb = _get(values.get("user_summary"), "bodyBatteryHighestValue")
    if bb:
        parts.append(f"Body Battery max {bb}")
    tr = values.get("training_readiness")
    morning = next((e for e in tr if isinstance(e, dict) and e.get("inputContext") == "AFTER_WAKEUP_RESET"),
                   None) if isinstance(tr, list) else None
    if morning and morning.get("score") is not None:
        parts.append(f"Bereitschaft morgens {morning['score']}")
    elif _get(tr, "score"):
        parts.append(f"Bereitschaft {_get(tr, 'score')}")
    return ", ".join(parts) if parts else "keine Kennzahlen"


def _weight_days(data) -> dict:
    """Garmins Wiegungen nach Tag: {YYYY-MM-DD: Tageszusammenfassung}. Tage ohne Wiegung fehlen."""
    out = {}
    for s in (data or {}).get("dailyWeightSummaries") or []:
        day = (s or {}).get("summaryDate")
        if day and (s.get("latestWeight") or s.get("allWeightMetrics")):
            out[day[:10]] = s
    return out


def sync_weight(g: Garmin, store: Store, start: date, end: date) -> int:
    """Wiegungen (Gewicht, BMI, ggf. Körperfett) als Tageswert 'weight' speichern.

    Ein Abruf liefert einen ganzen Zeitraum; geteilt in Jahresstücke. Rückgabe: Tage mit Wiegung.
    Wirft bei Fehler, damit der Aufrufer entscheiden kann (Historie erneut versuchen)."""
    n = 0
    for cs, ce in _chunks(start, end):
        days = _weight_days(g.get_weigh_ins(cs.isoformat(), ce.isoformat()))
        for day, summary in days.items():
            store.save_daily(day, "weight", summary)
        n += len(days)
        time.sleep(PAUSE_S)
    return n


def sync_daily(g: Garmin, store: Store, days_back: int) -> int:
    today = date.today()
    start = today - timedelta(days=days_back - 1)
    fetched = 0
    try:  # Gewicht: ein Abruf für den ganzen Zeitraum statt einer pro Tag
        n_w = sync_weight(g, store, start, today)
        fetched += 1
        log.info("Gewicht: %d Tage mit Wiegung (%s bis %s)", n_w, start, today)
    except Exception as e:
        log.warning("Gewicht fehlgeschlagen: %s", e)
    total = days_back
    log.info("Tageswerte: prüfe %d Tage (%s bis %s)", total, start, today)
    for i, d in enumerate(_days(start, today), 1):
        day = d.isoformat()
        recent = (today - d).days < REFRESH_RECENT_DAYS
        values = {}
        for source, fn in DAILY_SOURCES.items():
            if not recent and store.has_daily(day, source):
                continue
            try:
                values[source] = fn(g, day)
                store.save_daily(day, source, values[source])
                fetched += 1
            except Exception as e:  # einzelne Quelle darf den Rest nicht stoppen
                log.warning("Tageswert %s/%s fehlgeschlagen: %s", day, source, e)
            time.sleep(PAUSE_S)
        if values:
            log.info("[%d/%d] %s: %s", i, total, day, _day_summary(values))
    log.info("Tageswerte fertig: %d Abrufe gespeichert", fetched)
    return fetched


def _extract_fit(zip_bytes: bytes, target: Path) -> bool:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        fits = [n for n in z.namelist() if n.lower().endswith(".fit")]
        if not fits:
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(z.read(fits[0]))
        return True


def sync_activities(g: Garmin, store: Store, data_dir: Path, days_back: int, acts: list = None) -> int:
    today = date.today()
    start = today - timedelta(days=days_back - 1)
    if acts is None:
        log.info("Aktivitäten: suche von %s bis %s ...", start, today)
        acts = g.get_activities_by_date(start.isoformat(), today.isoformat())
        log.info("Aktivitäten: %d gefunden", len(acts))
    new_fits = 0
    for act in acts:
        aid = int(act["activityId"])
        rel = store.activity_fit_path(aid)
        if rel is None or not (data_dir / rel).exists():
            year = (act.get("startTimeLocal") or "0000")[:4]
            rel_path = Path("fit") / year / f"{aid}.fit"
            try:
                raw = g.download_activity(aid, dl_fmt=Garmin.ActivityDownloadFormat.ORIGINAL)
                if _extract_fit(raw, data_dir / rel_path):
                    rel = str(rel_path)
                    new_fits += 1
                    km = (act.get("distance") or 0) / 1000
                    mins = (act.get("duration") or 0) / 60
                    log.info(
                        "  FIT gespeichert: %s | %s | %s | %.1f km | %d min",
                        (act.get("startTimeLocal") or "")[:16],
                        (act.get("activityType") or {}).get("typeKey", "?"),
                        act.get("activityName") or "",
                        km,
                        mins,
                    )
                else:
                    log.warning("Aktivität %s: keine FIT-Datei im Download", aid)
                    rel = None
            except Exception as e:
                log.warning("FIT-Download für %s fehlgeschlagen: %s", aid, e)
                rel = None
            time.sleep(PAUSE_S)
        store.save_activity(act, rel)
    log.info("%d Aktivitäten geprüft, %d neue FIT-Dateien", len(acts), new_fits)
    return new_fits


# Zustände, die nicht an einen Tag gebunden sind. Ein Schnappschuss pro Tag ergibt die Historie.
def _snapshot_sources(today: date) -> dict:
    t = today.isoformat()
    return {
        "lactate_threshold": lambda g: g.get_lactate_threshold(latest=True),
        "race_predictions": lambda g: g.get_race_predictions(),
        "personal_records": lambda g: g.get_personal_record(),
        "heart_rate_zones": lambda g: g.get_heart_rate_zones(),
        "endurance_score": lambda g: g.get_endurance_score((today - timedelta(weeks=12)).isoformat(), t),
        "hill_score": lambda g: g.get_hill_score((today - timedelta(weeks=12)).isoformat(), t),
        "scheduled_workouts": lambda g: g.get_scheduled_workouts_by_period(
            (today - timedelta(days=14)).isoformat(), (today + timedelta(days=42)).isoformat()),
        "training_plans": lambda g: g.get_active_training_plans(),
        # Running Tolerance (Fenix 8): wie viel Laufbelastung der Bewegungsapparat gerade verträgt
        "running_tolerance": lambda g: g.get_running_tolerance(
            (today - timedelta(weeks=8)).isoformat(), t, "daily"),
    }


# Garmin erlaubt bei Verläufen höchstens ~1 Jahr pro Abfrage (Laktatschwelle und Prognosen 366 Tage,
# Endurance-Score 400 Tage). Längere Zeiträume werden deshalb in Jahresstücke geteilt.
HISTORY_CHUNK_DAYS = 365


def _chunks(start: date, end: date, size: int = HISTORY_CHUNK_DAYS):
    """Zeitraum in Stücke von höchstens `size` Tagen (beide Enden inklusive), älteste zuerst."""
    s = start
    while s <= end:
        e = min(s + timedelta(days=size - 1), end)
        yield s, e
        s = e + timedelta(days=1)


def _history_sources(start: date, end: date) -> list:
    """(Quelle, Stück-Beginn, Abruf) je Jahresstück. Gespeichert wird unter day = Stück-Beginn."""
    out = []
    for cs, ce in _chunks(start, end):
        s, e = cs.isoformat(), ce.isoformat()
        out += [
            ("lactate_threshold_history", s,
             lambda g, s=s, e=e: g.get_lactate_threshold(latest=False, start_date=s, end_date=e)),
            ("race_predictions_history", s, lambda g, s=s, e=e: g.get_race_predictions(s, e, "daily")),
            ("endurance_score_history", s, lambda g, s=s, e=e: g.get_endurance_score(s, e, "weekly")),
        ]
    return out


def sync_history_v07(g: Garmin, store: Store, days: int) -> bool:
    """Einmalig nachladen, was mit 0.7.0 dazukam: Wiegungen und Running-Tolerance-Verlauf.

    Rückgabe True, wenn alles geklappt hat (sonst versucht es der nächste volle Sync erneut)."""
    today = date.today()
    start = today - timedelta(days=days - 1)
    ok = True
    try:
        n = sync_weight(g, store, start, today)
        log.info("Gewicht-Historie: %d Tage mit Wiegung seit %s", n, start)
    except Exception as e:
        log.warning("Gewicht-Historie fehlgeschlagen: %s", e)
        ok = False
    for cs, ce in _chunks(start, today):
        s_, e_ = cs.isoformat(), ce.isoformat()
        try:
            data = g.get_running_tolerance(s_, e_, "weekly")
            store.save_snapshot(s_, "running_tolerance_history", data)
            log.info("Running-Tolerance-Verlauf ab %s: %s", s_, _size(data))
        except Exception as e:
            # Für Zeiträume vor der Fenix 8 gibt es keine Werte; das ist kein Fehler, der ein Wiederholen lohnt
            log.warning("Running-Tolerance-Verlauf ab %s fehlgeschlagen: %s", s_, e)
        time.sleep(PAUSE_S)
    return ok


def _size(data) -> str:
    if isinstance(data, list):
        return f"{len(data)} Einträge"
    if isinstance(data, dict):
        return f"{len(data)} Felder"
    return "leer" if data is None else "ok"


def sync_snapshots(g: Garmin, store: Store, history_days: int = 0) -> tuple[int, int]:
    """Schnappschüsse von heute; mit history_days zusätzlich einmal die Historie.

    Rückgabe: (gespeichert, fehlgeschlagene Verlaufs-Abrufe)."""
    today = date.today()
    jobs = [(src, today.isoformat(), fn) for src, fn in _snapshot_sources(today).items()]
    if history_days > 0:
        jobs += _history_sources(today - timedelta(days=history_days - 1), today)
    saved = history_failed = 0
    for source, day, fn in jobs:
        try:
            data = fn(g)
            store.save_snapshot(day, source, data)
            saved += 1
            log.info("Schnappschuss %s%s: %s", source, "" if day == today.isoformat() else f" ab {day}", _size(data))
        except Exception as e:  # einzelne Quelle darf den Rest nicht stoppen
            log.warning("Schnappschuss %s ab %s fehlgeschlagen: %s", source, day, e)
            if source.endswith("_history"):
                history_failed += 1
        time.sleep(PAUSE_S)
    return saved, history_failed


def run_sync(g: Garmin, store: Store, data_dir: Path, days_back: int, history_days: int = 0) -> dict:
    """Voller Sync. Rückgabe: {'meldung', 'neue_fit', 'verlauf_ok'}."""
    log_id = store.log_start()
    try:
        n_daily = sync_daily(g, store, days_back)
        n_fit = sync_activities(g, store, data_dir, days_back)
        n_snap, hist_failed = sync_snapshots(g, store, history_days)
        msg = f"{n_daily} Tageswerte, {n_fit} neue FIT-Dateien, {n_snap} Schnappschüsse"
        if history_days:
            msg += ", Verläufe vollständig" if not hist_failed else f", {hist_failed} Verlaufs-Abrufe fehlgeschlagen"
        store.log_finish(log_id, "ok", msg)
        log.info("Sync fertig: %s", msg)
        return {"meldung": msg, "neue_fit": n_fit, "verlauf_ok": history_days > 0 and hist_failed == 0}
    except Exception as e:
        store.log_finish(log_id, "error", repr(e))
        raise


def quick_check(g: Garmin, store: Store, data_dir: Path) -> dict:
    """Schnelle Prüfung auf neue Aktivitäten (gestern und heute), ein einziger Abruf.

    Nur wenn etwas Neues da ist: FIT-Dateien holen und die Tageswerte von heute auffrischen
    (Trainingsbereitschaft nach dem Training, Last). Ins Sync-Protokoll kommt nur ein Eintrag,
    wenn es etwas Neues gab."""
    today = date.today()
    acts = g.get_activities_by_date((today - timedelta(days=1)).isoformat(), today.isoformat())
    # Neu = noch gar nicht in der Datenbank. Fehlgeschlagene FIT-Downloads holt der nächste volle Sync nach.
    new = [a for a in acts if not store.has_activity(int(a["activityId"]))]
    if not new:
        log.debug("Schnellprüfung: nichts Neues (%d Aktivitäten)", len(acts))
        return {"meldung": "keine neuen Aktivitäten", "neue_fit": 0}
    log_id = store.log_start(kind="schnell")
    try:
        n_fit = sync_activities(g, store, data_dir, 2, acts=acts)
        n_daily = sync_daily(g, store, 1)
        msg = f"Schnellprüfung: {len(new)} neue Aktivität(en), {n_fit} FIT-Dateien, {n_daily} Tageswerte"
        store.log_finish(log_id, "ok", msg)
        log.info(msg)
        return {"meldung": msg, "neue_fit": n_fit}
    except Exception as e:
        store.log_finish(log_id, "error", repr(e))
        raise


def live_refresh(g: Garmin, store: Store) -> int:
    """Werte von heute auffrischen, die sich tagsüber ändern (Body Battery, Stress, Schritte,
    Trainingsbereitschaft). Schlaf und HRV nur, solange sie für heute noch fehlen.
    Kommt ohne Eintrag im Sync-Protokoll aus. Rückgabe: Zahl der Abrufe."""
    day = date.today().isoformat()
    sources = ["user_summary", "training_readiness"]
    sources += [s for s in ("sleep", "hrv") if not store.has_daily(day, s)]
    n = 0
    for source in sources:
        try:
            store.save_daily(day, source, DAILY_SOURCES[source](g, day))
            n += 1
        except Exception as e:
            log.warning("Live-Wert %s fehlgeschlagen: %s", source, e)
        time.sleep(PAUSE_S)
    log.debug("Live-Werte von heute aufgefrischt (%s)", ", ".join(sources))
    return n
