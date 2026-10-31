"""MCP-Server: macht garmin.db für Claude zugänglich.

Bis auf die Notizen ist alles nur lesend. Jeder Werkzeugaufruf wird mit Dauer,
Ergebnisgröße und Status protokolliert, im Add-on-Protokoll und in der Tabelle mcp_log.
"""

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

log = logging.getLogger("garmin_coach.mcp")

PORT = 8099
PATH = "/mcp"
VERSION = "0.4.0"

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
        for key in ("zeilen", "werte", "wochen", "eintraege", "quellen"):
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
    akklimatisierung, Notizen."""
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
def garmin_profile(source: str | None = None, day: str | None = None) -> dict:
    """Leistungsprofil aus Garmin Connect als Schnappschuss (einer pro Tag, daraus entsteht die Historie).

    Ohne source: Liste der Quellen. Quellen: lactate_threshold, race_predictions, personal_records,
    heart_rate_zones, endurance_score, hill_score, scheduled_workouts, training_plans sowie die
    einmalig geladenen Verläufe lactate_threshold_history, race_predictions_history,
    endurance_score_history. Rohdaten von Garmin (Tempo in m/s, Zeiten in s)."""
    return _call("garmin_profile", queries.garmin_profile, source=source, day=day)


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


@server.tool(annotations=READ_ONLY)
def describe_schema() -> dict:
    """Tabellen und Spalten der Datenbank, als Grundlage für query_sql."""
    return _call("describe_schema", queries.schema)


@server.tool(annotations=READ_ONLY)
def query_sql(sql: str) -> dict:
    """Freie, rein lesende SQL-Abfrage (SQLite, nur SELECT/WITH, max. 2000 Zeilen, 15 s).

    Wichtige Tabellen: fit_sessions (pro Einheit), fit_laps (pro Runde), fit_records
    (Sekundenwerte, t = Sekunden seit Start), activities (data = Garmin-JSON), daily_raw
    (Tageswerte als JSON, mit json_extract auslesbar), garmin_snapshots, notes, mcp_log.
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
    log.info("MCP-Server %s startet auf Port %d (Pfad %s)", VERSION, PORT, PATH)
    # Kein Zugriffsprotokoll pro Anfrage; die Werkzeugaufrufe protokollieren wir selbst.
    config = uvicorn.Config(app, host="0.0.0.0", port=PORT, access_log=False, log_level="warning")
    await uvicorn.Server(config).serve()
