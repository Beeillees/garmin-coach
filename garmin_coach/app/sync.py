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


def sync_daily(g: Garmin, store: Store, days_back: int) -> int:
    today = date.today()
    start = today - timedelta(days=days_back - 1)
    fetched = 0
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


def sync_activities(g: Garmin, store: Store, data_dir: Path, days_back: int) -> int:
    today = date.today()
    start = today - timedelta(days=days_back - 1)
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
    }


def _history_sources(start: date, end: date) -> dict:
    s, e = start.isoformat(), end.isoformat()
    return {
        "lactate_threshold_history": lambda g: g.get_lactate_threshold(latest=False, start_date=s, end_date=e),
        "race_predictions_history": lambda g: g.get_race_predictions(s, e, "daily"),
        "endurance_score_history": lambda g: g.get_endurance_score(s, e, "weekly"),
    }


def _size(data) -> str:
    if isinstance(data, list):
        return f"{len(data)} Einträge"
    if isinstance(data, dict):
        return f"{len(data)} Felder"
    return "leer" if data is None else "ok"


def sync_snapshots(g: Garmin, store: Store, history_days: int = 0) -> int:
    """Schnappschüsse von heute; mit history_days zusätzlich einmal die Historie."""
    today = date.today()
    sources = dict(_snapshot_sources(today))
    if history_days > 0:
        sources.update(_history_sources(today - timedelta(days=history_days), today))
    saved = 0
    for source, fn in sources.items():
        try:
            data = fn(g)
            store.save_snapshot(today.isoformat(), source, data)
            saved += 1
            log.info("Schnappschuss %s: %s", source, _size(data))
        except Exception as e:  # einzelne Quelle darf den Rest nicht stoppen
            log.warning("Schnappschuss %s fehlgeschlagen: %s", source, e)
        time.sleep(PAUSE_S)
    return saved


def run_sync(g: Garmin, store: Store, data_dir: Path, days_back: int, history_days: int = 0) -> str:
    log_id = store.log_start()
    try:
        n_daily = sync_daily(g, store, days_back)
        n_fit = sync_activities(g, store, data_dir, days_back)
        n_snap = sync_snapshots(g, store, history_days)
        msg = f"{n_daily} Tageswerte, {n_fit} neue FIT-Dateien, {n_snap} Schnappschüsse"
        store.log_finish(log_id, "ok", msg)
        log.info("Sync fertig: %s", msg)
        return msg
    except Exception as e:
        store.log_finish(log_id, "error", repr(e))
        raise
