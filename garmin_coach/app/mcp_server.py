"""MCP-Server: macht garmin.db für Claude zugänglich.

Schreibend sind nur Notizen, Tagebuch, Trainingsplan, Wettkämpfe und Coach-Berichte; Garmin selbst wird
nie verändert. Jeder Werkzeugaufruf wird mit Dauer,
Ergebnisgröße und Status protokolliert, im Add-on-Protokoll und in der Tabelle mcp_log.
"""

import asyncio
import json
import logging
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

import queries
import scheduler

log = logging.getLogger("garmin_coach.mcp")

PORT = 8099
PATH = "/mcp"
STATE_PATH = "/ha/state"
VERSION = "0.8.4"

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True,
                            open_world_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False,
                        open_world_hint=False)
DELETE = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True,
                         open_world_hint=False)

LOG_PARAM_CHARS = 300      # SQL/Parameter im INFO-Protokoll kürzen
SLOW_MS = 2000             # langsame Aufrufe als Warnung markieren
LOG_RETENTION_DAYS = 90

INSTRUCTIONS = """\
Garmin Coach: lokale Trainings- und Gesundheitsdaten von Bruno (Garmin Fenix 8 + HRM Pro Plus).

Empfohlener Einstieg: data_overview, dann list_activities bzw. daily_metrics.
- Einheiten in den Antworten: km, Minuten, Pace in min/km, Kadenz in Schritten/min (Laufen).
- In der Datenbank (für query_sql): Tempo in m/s, Distanz in m, Zeiten in s,
  Schrittlänge/vertikale Bewegung in mm, Bodenkontakt in ms.
- Sportarten: Feld sport_gruppe (laufen, rad, schwimmen, kraft_hiit, gehen, kein_sport, sonstiges);
  in query_sql als Funktion sport_gruppe(type_key, fit_sport).
- Einheiten ohne FIT-Auswertung haben Werte aus Garmin Connect (quelle = garmin_json).
- Drift (Pa:HR) ist nur bei gleichmäßigen Läufen aussagekräftig (tempo_cv_pct klein, etwa < 8 %).
- Trainingsbereitschaft: 'bereitschaft' = Wert nach dem Aufwachen, 'bereitschaft_abend' = letzter
  Wert des Tages, 'regeneration_nacht' = Morgenwert minus Abendwert des Vortags.
- Frühwarnzeichen Infekt: hrv_nacht unter Basisbereich, ruhepuls und atem_schlaf erhöht,
  haut_temp_abweichung positiv, stress_schlaf hoch.
- Notizen (add_note) halten Krankheit, Verletzung, Reise, Stress fest und erscheinen bei den Tagen.
- Tageswerte stammen aus Garmin Connect, Einheiten aus den Original-FIT-Dateien.
- Gewicht: Bruno wiegt sich täglich und trägt es in Garmin ein; gewicht_kg in daily_metrics
  (Tage ohne Wiegung fehlen). Für W/kg, Trends und Flüssigkeitsverlust bei langen Einheiten.
- Running Tolerance (Fenix 8): garmin_profile(source="running_tolerance"), Verlauf in
  running_tolerance_history; zeigt, wie viel Laufbelastung der Bewegungsapparat gerade verträgt.
- Coaching: get_plan liefert Brunos Trainingsplan und Wettkämpfe. Neue Einheiten ohne Bericht zeigt
  pending_reports; nach einem Bericht save_report aufrufen, damit nichts doppelt ausgewertet wird.
  Die Kurzfassung (summary) erscheint im HA-Dashboard: schlankes Markdown ohne Tabellen, Aufbau siehe save_report.
- Tagebuch (log_diary): was Garmin nicht misst – Beine (1 frisch … 5 sehr schwer), Energie
  (1 leer … 5 top), Infektzeichen, Schmerz mit Körperstelle (0–10), Verpflegung g KH/h, Freitext.
  Eintragen, wenn Bruno so etwas im Chat erzählt; erscheint in daily_metrics und get_activity.
  Immer original = Brunos Nachricht im Wortlaut mitgeben, text = deine Zusammenfassung.
- Rückblicke: review_data(kind=woche|monat) liefert alles für einen Wochen-/Monatsrückblick in
  einem Abruf; danach save_report(kind=woche|monat, period=…). pending_reports zeigt, ob sie fehlen.
- Aktuelle Daten: sync_now (schnell = nur neue Aktivitäten, voll = alles). Sonst läuft der Sync
  zu festen Zeiten plus Schnellprüfung tagsüber.
"""

server = MCPServer(name="garmin-coach", title="Garmin Coach", instructions=INSTRUCTIONS, version=VERSION)


# --- Protokoll ----------------------------------------------------------------

_log_lock = threading.Lock()
_log_conn = None
_log_count = 0


def _log_db():
    global _log_conn
    if _log_conn is None:
        _log_conn = sqlite3.connect(str(queries.DB_FILE), timeout=10, check_same_thread=False)
        _prune_log(_log_conn)
    return _log_conn


def _prune_log(conn) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=LOG_RETENTION_DAYS)).isoformat(timespec="seconds")
    conn.execute("DELETE FROM mcp_log WHERE at < ?", (cutoff,))
    conn.commit()


def _write_log(tool: str, params: str, ms: int, rows, status: str, error: str | None) -> None:
    global _log_count
    try:
        with _log_lock:
            conn = _log_db()
            conn.execute(
                "INSERT INTO mcp_log(at, tool, params, duration_ms, result_rows, status, error) "
                "VALUES(?,?,?,?,?,?,?)",
                (datetime.now(timezone.utc).isoformat(timespec="seconds"), tool, params[:4000], ms, rows,
                 status, error and error[:2000]))
            conn.commit()
            _log_count += 1
            if _log_count % 500 == 0:
                _prune_log(conn)
    except Exception as e:  # Protokoll darf die Abfrage nie stören
        log.debug("mcp_log nicht geschrieben: %s", e)


def _result_size(result) -> tuple[int | None, bool]:
    """(Anzahl Zeilen/Einträge, abgeschnitten)."""
    if isinstance(result, list):
        return len(result), False
    if isinstance(result, dict):
        for key in ("zeilen", "werte", "wochen", "eintraege", "quellen", "tage"):
            if isinstance(result.get(key), list):
                return len(result[key]), bool(result.get("abgeschnitten"))
        return 1, bool(result.get("abgeschnitten"))
    return None, False


def _empty(result) -> bool:
    if isinstance(result, list):
        return not result
    if isinstance(result, dict):
        for key in ("zeilen", "werte", "wochen", "eintraege"):
            if key in result:
                return not result[key]
    return False


def _fmt_params(params: dict) -> str:
    parts = []
    for k, v in params.items():
        if v is None:
            continue
        s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
        parts.append(f"{k}={' '.join(s.split())}")
    return ", ".join(parts)


WRITE_TOOLS_NOTIFY = {"save_report", "log_diary", "delete_diary"}


def _notify_ha(tool: str) -> None:
    """Nach Berichten und Tagebuch die HA-Sensoren sofort auffrischen (im Hintergrund)."""
    def run():
        import ha_setup  # spät importieren: ha_setup importiert dieses Modul
        ha_setup.notify_update(tool)
    threading.Thread(target=run, name="notify_ha", daemon=True).start()


def _call(tool: str, fn, **params) -> Any:
    full = _fmt_params(params)
    short = full if len(full) <= LOG_PARAM_CHARS else full[:LOG_PARAM_CHARS] + " …"
    log.debug("MCP %s | Start | %s", tool, full)
    t0 = time.monotonic()
    try:
        result = fn(**params)
    except Exception as e:  # dem Modell eine verständliche Fehlermeldung geben statt eines Stacktrace
        ms = int((time.monotonic() - t0) * 1000)
        log.warning("MCP %s | Fehler | %d ms | %s | %s", tool, ms, e, full)
        _write_log(tool, full, ms, None, "fehler", str(e))
        return {"fehler": str(e)}
    ms = int((time.monotonic() - t0) * 1000)
    rows, truncated = _result_size(result)
    status = "abgeschnitten" if truncated else ("leer" if _empty(result) else "ok")
    level = logging.WARNING if ms > SLOW_MS else logging.INFO
    log.log(level, "MCP %s | %s Zeilen | %d ms%s | %s%s", tool, "?" if rows is None else rows, ms,
            " (langsam)" if ms > SLOW_MS else "", status, f" | {short}" if short else "")
    _write_log(tool, full, ms, rows, status, None)
    if tool in WRITE_TOOLS_NOTIFY and not (isinstance(result, dict) and result.get("doppelt")):
        _notify_ha(tool)
    return result


# --- Werkzeuge ----------------------------------------------------------------

@server.tool(annotations=READ_ONLY)
def data_overview() -> dict:
    """Überblick über den Datenstand: Zeiträume, Anzahl Aktivitäten/Tage, Fortschritt der
    FIT-Auswertung, Sportart-Gruppen über alle Aktivitäten, Notizen, Schnappschüsse, letzter Sync."""
    return _call("data_overview", queries.overview)


@server.tool(annotations=READ_ONLY)
def list_activities(start_date: str | None = None, end_date: str | None = None,
                    sport: str | None = None, limit: int = 50) -> list | dict:
    """Aktivitäten mit Kernwerten, neueste zuerst. Auch nicht ausgewertete Einheiten haben
    Pace, Last, Trainingseffekt, Pulszonen usw. (aus Garmin Connect, quelle = garmin_json).

    start_date/end_date im Format YYYY-MM-DD (lokales Datum, beide inklusive).
    sport: Gruppe (laufen, rad, schwimmen, kraft_hiit, gehen, kein_sport, sonstiges) oder
    Garmin-Typ (z. B. treadmill_running, hiit). limit max. 500.
    Leere Werte werden weggelassen.
    """
    return _call("list_activities", queries.list_activities, start_date=start_date, end_date=end_date,
                 sport=sport, limit=limit)


@server.tool(annotations=READ_ONLY)
def get_activity(activity_id: int) -> dict:
    """Alle Details einer Einheit: Gesamtwerte, Pulszonen, jede Runde (Pace, Puls, Kadenz,
    Schrittlänge, Bodenkontakt, vertikale Bewegung, Watt, Atmung), Workout-Schritte mit
    Zielvorgaben, Schwimmbahnen und Sätze. Block 'garmin' mit Zusatzwerten aus Garmin Connect:
    steigungsbereinigte Pace, Body-Battery-Verbrauch, Trainingseffekt-Label, schnellste
    Abschnitte, Bestzeit, Schweißverlust, Atemfrequenz, Leistungszonen. Dazu Notizen des Tages."""
    return _call("get_activity", queries.get_activity, activity_id=activity_id)


@server.tool(annotations=READ_ONLY)
def get_activity_timeseries(activity_id: int, resolution_s: int = 10, fields: list[str] | None = None,
                            start_s: int | None = None, end_s: int | None = None) -> dict:
    """Verlauf einer Einheit (z. B. Pulskurve), gemittelt über resolution_s Sekunden.

    fields aus: hr, max_hr, speed, distance_m, altitude_m, cadence, power, gct_ms,
    gct_balance_pct, vo_mm, vr_pct, step_length_mm, resp, temp.
    Standard: hr, speed, cadence, power, altitude_m. start_s/end_s schneiden einen Abschnitt aus.
    Max. 2000 Punkte; für lange Einheiten resolution_s erhöhen. Nur für ausgewertete FIT-Dateien.
    """
    return _call("get_activity_timeseries", queries.timeseries, activity_id=activity_id,
                 resolution_s=resolution_s, fields=fields, start_s=start_s, end_s=end_s)


@server.tool(annotations=READ_ONLY)
def daily_metrics(start_date: str | None = None, end_date: str | None = None) -> list | dict:
    """Tageswerte. Standard: letzte 28 Tage. Datum im Format YYYY-MM-DD.

    HRV (Nacht, 7 Tage, Status, Basisbereich), Ruhepuls, Schlaf (Dauer, Tief, REM, Wachzeit,
    Beginn/Ende, Schlafwert, Unruhe, Stress im Schlaf), Atmung (Schlaf, wach), Hauttemperatur-
    Abweichung, SpO2, Atemaussetzer, Body Battery (max, min, aufgeladen, beim Aufwachen, über Nacht),
    Stress (Schnitt, Minuten je Stufe), Intensitätsminuten, Schritte,
    Trainingsbereitschaft morgens und abends mit Erholungszeit, Tagesbilanz, Regeneration über
    Nacht und Faktoren, Trainingsstatus mit akuter/chronischer Last, VO2max, Hitze-/Höhen-
    akklimatisierung, Gewicht (kg, BMI, ggf. Körperfett/-wasser, Muskelmasse), Notizen."""
    return _call("daily_metrics", queries.daily_metrics, start_date=start_date, end_date=end_date)


@server.tool(annotations=READ_ONLY)
def readiness_detail(day: str | None = None) -> dict:
    """Alle Einträge der Trainingsbereitschaft eines Tages (Standard: heute) mit Uhrzeit, Anlass
    (aufwachen, nach_training, zwischenstand), Score, Erholungszeit, akuter Last und Faktoren,
    dazu die Einheiten des Tages. Zeigt, was welche Einheit gekostet hat."""
    return _call("readiness_detail", queries.readiness_detail, day=day)


@server.tool(annotations=READ_ONLY)
def weekly_summary(weeks: int = 12, sport: str | None = None) -> dict:
    """Wochenumfänge (ISO-Wochen) pro Sportart-Gruppe: Einheiten, km, Stunden, Trainingslast,
    Höhenmeter, Schnitt-Puls, Pace beim Laufen, dazu Wochensumme und Notizen.
    sport: Gruppe oder Garmin-Typ wie bei list_activities."""
    return _call("weekly_summary", queries.weekly_summary, weeks=weeks, sport=sport)


@server.tool(annotations=READ_ONLY)
def garmin_profile(source: str | None = None, day: str | None = None, kompakt: bool = True) -> dict:
    """Leistungsprofil aus Garmin Connect als Schnappschuss (einer pro Tag, daraus entsteht die Historie).

    Ohne source: Liste der Quellen. Quellen: lactate_threshold, race_predictions, personal_records,
    heart_rate_zones, endurance_score, hill_score, scheduled_workouts, training_plans,
    running_tolerance (letzte 8 Wochen, täglich) sowie die einmalig geladenen Verläufe
    lactate_threshold_history, race_predictions_history, endurance_score_history,
    running_tolerance_history (wöchentlich). Verläufe kommen standardmäßig verdichtet (Laktatschwelle nur Änderungen
    mit Pace, Prognosen ein Wert pro Woche); kompakt=False liefert die Rohdaten (sehr groß).
    Rohdaten von Garmin (Zeiten in s).
    lactate_threshold hat zusätzlich 'auswertung' mit Schwellen-HF, -Pace und FTP; Achtung, im
    Rohwert steht das Tempo in 0,1 m/s."""
    return _call("garmin_profile", queries.garmin_profile, source=source, day=day, kompakt=kompakt)


@server.tool(annotations=READ_ONLY)
def list_notes(start_date: str | None = None, end_date: str | None = None) -> list | dict:
    """Notizen (Krankheit, Verletzung, Reise, Stress, Sonstiges), optional für einen Zeitraum."""
    return _call("list_notes", queries.list_notes, start_date=start_date, end_date=end_date)


@server.tool(annotations=WRITE)
def add_note(day_from: str, text: str, category: str = "sonstiges", day_to: str | None = None) -> dict:
    """Notiz anlegen, damit Ausreißer in den Daten eingeordnet werden können.

    day_from/day_to im Format YYYY-MM-DD (day_to optional, sonst ein Tag).
    category: krank, verletzt, reise, stress oder sonstiges. Nur anlegen, wenn Bruno es sagt
    oder bestätigt."""
    return _call("add_note", queries.add_note, day_from=day_from, text=text, category=category, day_to=day_to)


@server.tool(annotations=DELETE)
def delete_note(note_id: int) -> dict:
    """Notiz löschen (z. B. bei Tippfehler). Nur auf Brunos Wunsch."""
    return _call("delete_note", queries.delete_note, note_id=note_id)


# --- Tagebuch -----------------------------------------------------------------

@server.tool(annotations=WRITE)
def log_diary(day: str | None = None, activity_id: int | None = None, beine: int | None = None,
              energie: int | None = None, infekt: str | None = None, schmerz: str | None = None,
              schmerz_staerke: int | None = None, kh_g_pro_h: float | None = None,
              fluessigkeit_ml_pro_h: float | None = None, text: str | None = None,
              entry_id: int | None = None, original: str | None = None,
              original_anhaengen: bool = True) -> dict:
    """Tagebucheintrag zu dem, was Garmin nicht misst. Eintragen, wenn Bruno es im Chat erzählt.

    original: IMMER mitgeben – Brunos Nachricht(en) dazu im Wortlaut, unverändert und ungekürzt
    (auch Tippfehler, Diktierfehler, Umgangssprache). text ist deine Zusammenfassung, original
    das Rohmaterial. Beim Ergänzen mit entry_id wird original an den vorhandenen Wortlaut
    angehängt (original_anhaengen=False ersetzt ihn, nur zum Korrigieren auf Brunos Wunsch).

    day YYYY-MM-DD (Standard heute; bei activity_id der Tag der Einheit). Alle Felder optional:
    beine 1 (frisch) … 5 (sehr schwer); energie 1 (leer) … 5 (top); infekt: Symptome als Text;
    schmerz: Körperstelle und Art, schmerz_staerke 0–10; kh_g_pro_h und fluessigkeit_ml_pro_h
    bei langen Einheiten; text: alles Weitere in Brunos Worten. Mehrere Einträge pro Tag möglich.
    Mit entry_id wird ein bestehender Eintrag ergänzt bzw. geändert (nur angegebene Felder)."""
    return _call("log_diary", queries.log_diary, day=day, activity_id=activity_id, beine=beine,
                 energie=energie, infekt=infekt, schmerz=schmerz, schmerz_staerke=schmerz_staerke,
                 kh_g_pro_h=kh_g_pro_h, fluessigkeit_ml_pro_h=fluessigkeit_ml_pro_h, text=text,
                 entry_id=entry_id, original=original, original_anhaengen=original_anhaengen)


@server.tool(annotations=READ_ONLY)
def list_diary(start_date: str | None = None, end_date: str | None = None) -> list | dict:
    """Tagebucheinträge eines Zeitraums (Standard: letzte 28 Tage), mit Brunos Wortlaut (original)."""
    return _call("list_diary", queries.list_diary, start_date=start_date, end_date=end_date)


@server.tool(annotations=DELETE)
def delete_diary(entry_id: int) -> dict:
    """Tagebucheintrag löschen (z. B. Fehleintrag). Nur auf Brunos Wunsch."""
    return _call("delete_diary", queries.delete_diary, entry_id=entry_id)


# --- Coaching -----------------------------------------------------------------

@server.tool(annotations=READ_ONLY)
def get_plan() -> dict:
    """Brunos aktueller Trainingsplan (Text, neueste Version), heutiger Wochentag und Wettkämpfe
    mit Abstand in Tagen/Wochen. Vor jedem Morgen-/Abendbericht lesen."""
    return _call("get_plan", queries.get_plan)


@server.tool(annotations=WRITE)
def set_plan(text: str) -> dict:
    """Trainingsplan als Text speichern (Markdown). Ersetzt den bisherigen Plan; alte Versionen
    bleiben in der Tabelle coach_plan erhalten. Immer den vollständigen Plan schicken, nicht nur
    die Änderung. Nur auf Brunos Wunsch oder nach seiner Bestätigung."""
    return _call("set_plan", queries.set_plan, text=text)


@server.tool(annotations=WRITE)
def set_race(day: str, name: str, sport: str | None = None, distance_km: float | None = None,
             priority: str | None = None, status: str = "geplant", note: str | None = None,
             race_id: int | None = None) -> dict:
    """Wettkampf anlegen oder (mit race_id) ändern. day im Format YYYY-MM-DD.
    priority: A (Hauptziel), B, C. status: geplant, abgesagt, erledigt."""
    return _call("set_race", queries.set_race, day=day, name=name, sport=sport, distance_km=distance_km,
                 priority=priority, status=status, note=note, race_id=race_id)


@server.tool(annotations=DELETE)
def delete_race(race_id: int) -> dict:
    """Wettkampf löschen (bei Fehleintrag). Abgesagte Rennen lieber mit status=abgesagt behalten."""
    return _call("delete_race", queries.delete_race, race_id=race_id)


@server.tool(annotations=READ_ONLY)
def pending_reports(days: int = 2) -> dict:
    """Was ist noch nicht besprochen: Einheiten der letzten `days` Tage ohne Trainingsbericht
    (Atemübungen ausgenommen), ob Morgen- und Abendbericht für heute fehlen, ob der Wochen- und
    Monatsrückblick für den fälligen Zeitraum fehlt, letzter Sync."""
    return _call("pending_reports", queries.pending_reports, days=days)


@server.tool(annotations=WRITE)
def save_report(kind: str, summary: str, activity_id: int | None = None, day: str | None = None,
                period: str | None = None, ersetzen: bool = False) -> dict:
    """Bericht vermerken. kind: morgen, training (braucht activity_id), abend, woche oder monat.
    period: '2026-W44' bzw. '2026-10' (Standard: der fällige Zeitraum wie in pending_reports).
    Pro Aktivität bzw. Zeitraum nur einmal; ersetzen=True überschreibt den vorhandenen Bericht
    (Zeitraum, Einheit bzw. letzter Bericht der Art an diesem Tag).

    summary erscheint im Home-Assistant-Dashboard (Markdown-Karte). Die Kennzahlen (Bereitschaft,
    HRV, Ruhepuls, Schlaf, Body Battery) zeigt die Karte selbst live aus den Sensoren – deshalb
    KEINE Tabelle und keine Zahlenkolonnen, nur die Einschätzung. Immer schlankes Markdown, auf
    Deutsch, keine englischen Codes (LOW, STRAINED_4):
      morgen/abend:
        ### <Ampel 🟢/🟡/🟠/🔴> <Kernaussage in wenigen Worten>
        **Einordnung:** 1–2 Sätze, warum (Zahlen nur, wenn sie die Aussage tragen).
        **Heute** (abends: **Morgen**) – 2–4 Stichpunkte mit konkreten Vorgaben (Dauer, HF, Pace)
        optional **Ausblick** – 1–2 Stichpunkte
      training:
        ### <Emoji> <Einheit – Bewertung in wenigen Worten>
        **Bewertung:** 1–2 Sätze. **Mitnehmen** – 1–3 Stichpunkte.
      woche/monat:
        ### <Ampel> <Zeitraum – Kernaussage>
        Abschnitte als Stichpunkte: **Umfang & Last** (Kernzahlen gegen Vorperiode, knapp),
        **Erholung**, **Highlights**, **Probleme**, **Plan gegen Ist**, **Fokus nächste Woche bzw.
        nächster Monat**, **Beim nächsten Rückblick prüfen**. Ausführlich genug, dass der nächste
        Rückblick darauf aufbaut (bis ca. 2000 Zeichen)."""
    return _call("save_report", queries.save_report, kind=kind, summary=summary, activity_id=activity_id,
                 day=day, period=period, ersetzen=ersetzen)


@server.tool(annotations=READ_ONLY)
def review_data(kind: str = "woche", period: str | None = None) -> dict:
    """Alles für einen Rückblick in einem Abruf. kind: woche oder monat; period: '2026-W44' bzw.
    '2026-10' (Standard: am Sonntag die laufende Woche, sonst die letzte; Monat ab dem 28. der
    laufende, sonst der Vormonat).
    Enthält: Umfang je Sportart mit Vorperiode, Laufen (km, längster Lauf, Höhenmeter),
    Schlüsseleinheiten (hart/lang mit Pace, Puls, Last, RPE), Erholung (Bereitschaft, HRV gegen
    Basis, Ruhepuls, Schlaf, Last, VO2max) mit Vorperiode, Tagebuch, Notizen, Berichte des
    Zeitraums, letzter Rückblick derselben Art, Trainingsplan und kommende Wettkämpfe.
    Woche: Tagesübersicht; Monat: Übersicht je Woche."""
    return _call("review_data", queries.review_data, kind=kind, period=period)


@server.tool(annotations=READ_ONLY)
def list_reports(days: int = 14, kind: str | None = None) -> list | dict:
    """Frühere Coach-Berichte (Kurzfassung) der letzten `days` Tage, optional nur eine Art."""
    return _call("list_reports", queries.list_reports, days=days, kind=kind)


def _sync_now(mode: str) -> dict:
    mode = (mode or "schnell").strip().lower()
    if mode not in ("schnell", "voll"):
        raise ValueError("mode muss 'schnell' oder 'voll' sein")
    if not scheduler.worker_ready.is_set():
        raise RuntimeError("Sammler läuft noch nicht (Anmeldung bei Garmin oder Backfill läuft)")
    result = scheduler.request(mode, timeout_s=150 if mode == "voll" else 90)
    if result is None:
        return {"status": "läuft noch", "hinweis": "Ergebnis kam nicht rechtzeitig; data_overview zeigt den Stand."}
    return {"status": "fehler" if "fehler" in result else "ok", **result}


@server.tool(annotations=WRITE)
async def sync_now(mode: str = "schnell") -> dict:
    """Daten sofort von Garmin holen und warten, bis sie da sind.
    mode=schnell: nur neue Aktivitäten (plus FIT-Auswertung und Tageswerte von heute), wenige Sekunden.
    mode=voll: alle Tageswerte der letzten Tage, Aktivitäten und Profil-Schnappschüsse, ca. 1 min.
    Nur nutzen, wenn eine erwartete Einheit oder heutige Werte fehlen."""
    return await asyncio.to_thread(_call, "sync_now", _sync_now, mode=mode)


@server.tool(annotations=READ_ONLY)
def describe_schema() -> dict:
    """Tabellen und Spalten der Datenbank, als Grundlage für query_sql."""
    return _call("describe_schema", queries.schema)


@server.tool(annotations=READ_ONLY)
def query_sql(sql: str) -> dict:
    """Freie, rein lesende SQL-Abfrage (SQLite, nur SELECT/WITH, max. 2000 Zeilen, 15 s).

    Wichtige Tabellen: fit_sessions (pro Einheit), fit_laps (pro Runde), fit_records
    (Sekundenwerte, t = Sekunden seit Start), activities (data = Garmin-JSON), daily_raw
    (Tageswerte als JSON, mit json_extract auslesbar), garmin_snapshots, notes, coach_plan, races,
    coach_reports, sync_log, mcp_log.
    Funktion sport_gruppe(type_key, fit_sport). Erst describe_schema aufrufen, wenn Spalten unklar sind.
    """
    return _call("query_sql", queries.run_sql, sql=sql)


async def serve() -> None:
    import uvicorn

    logging.getLogger("mcp").setLevel(logging.WARNING)
    app = server.streamable_http_app(
        streamable_http_path=PATH,
        json_response=True,
        stateless_http=True,
        # Zugriff kommt nur über das interne HA-Netz bzw. den Webhook; Host-Prüfung würde
        # die interne Adresse des Add-ons ablehnen.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        host="0.0.0.0",
    )
    # Sensoren für die HA-Integration (nur intern erreichbar, nicht über den Webhook)
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    import ha_state

    async def ha_state_endpoint(request):
        try:
            data = await asyncio.to_thread(ha_state.build)
            data["version"] = VERSION
            return JSONResponse(data)
        except Exception as e:  # HA soll eine klare Meldung bekommen statt eines Abbruchs
            log.exception("Sensoren für HA nicht erzeugt")
            return JSONResponse({"fehler": str(e)}, status_code=500)

    app.router.routes.append(Route(STATE_PATH, ha_state_endpoint, methods=["GET"]))
    log.info("MCP-Server %s startet auf Port %d (Pfad %s, Sensoren %s)", VERSION, PORT, PATH, STATE_PATH)
    # Kein Zugriffsprotokoll pro Anfrage; die Werkzeugaufrufe protokollieren wir selbst.
    config = uvicorn.Config(app, host="0.0.0.0", port=PORT, access_log=False, log_level="warning")
    await uvicorn.Server(config).serve()
