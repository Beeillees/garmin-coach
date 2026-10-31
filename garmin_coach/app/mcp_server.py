"""MCP-Server: macht garmin.db für Claude lesbar (nur lesend)."""

import logging
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

import queries

log = logging.getLogger("garmin_coach.mcp")

PORT = 8099
PATH = "/mcp"

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True,
                            open_world_hint=False)

INSTRUCTIONS = """\
Garmin Coach: lokale Trainings- und Gesundheitsdaten von Bruno (Garmin Fenix 8 + HRM Pro Plus).

Empfohlener Einstieg: data_overview, dann list_activities bzw. daily_metrics.
- Einheiten in den Antworten: km, Minuten, Pace in min/km, Kadenz in Schritten/min (Laufen).
- In der Datenbank (für query_sql): Tempo in m/s, Distanz in m, Zeiten in s,
  Schrittlänge/vertikale Bewegung in mm, Bodenkontakt in ms.
- Drift (Pa:HR) ist nur bei gleichmäßigen Läufen aussagekräftig (tempo_cv_pct klein, etwa < 8 %).
- Tageswerte stammen aus Garmin Connect, Einheiten aus den Original-FIT-Dateien.
"""

server = MCPServer(name="garmin-coach", title="Garmin Coach", instructions=INSTRUCTIONS, version="0.3.0")


def _safe(fn, *args, **kwargs) -> Any:
    log.info("Abfrage: %s%s", fn.__name__, tuple(a for a in args if a is not None) or "")
    try:
        return fn(*args, **kwargs)
    except Exception as e:  # dem Modell eine verständliche Fehlermeldung geben statt eines Stacktrace
        log.warning("Werkzeug %s fehlgeschlagen: %s", fn.__name__, e)
        return {"fehler": str(e)}


@server.tool(annotations=READ_ONLY)
def data_overview() -> dict:
    """Überblick über den Datenstand: Zeiträume, Anzahl Aktivitäten/Tage, Sportarten, letzter Sync."""
    return _safe(queries.overview)


@server.tool(annotations=READ_ONLY)
def list_activities(start_date: str | None = None, end_date: str | None = None,
                    sport: str | None = None, limit: int = 50) -> list | dict:
    """Aktivitäten mit Kernwerten, neueste zuerst.

    start_date/end_date im Format YYYY-MM-DD (lokales Datum, beide inklusive).
    sport z. B. 'running', 'cycling', 'swimming', 'hiit', 'floor_climbing'. limit max. 500.
    """
    return _safe(queries.list_activities, start_date, end_date, sport, limit)


@server.tool(annotations=READ_ONLY)
def get_activity(activity_id: int) -> dict:
    """Alle Details einer Einheit: Gesamtwerte, Pulszonen, jede Runde (Pace, Puls, Kadenz,
    Schrittlänge, Bodenkontakt, vertikale Bewegung, Watt, Atmung), Workout-Schritte mit
    Zielvorgaben, Schwimmbahnen und Sätze."""
    return _safe(queries.get_activity, activity_id)


@server.tool(annotations=READ_ONLY)
def get_activity_timeseries(activity_id: int, resolution_s: int = 10, fields: list[str] | None = None,
                            start_s: int | None = None, end_s: int | None = None) -> dict:
    """Verlauf einer Einheit (z. B. Pulskurve), gemittelt über resolution_s Sekunden.

    fields aus: hr, max_hr, speed, distance_m, altitude_m, cadence, power, gct_ms,
    gct_balance_pct, vo_mm, vr_pct, step_length_mm, resp, temp.
    Standard: hr, speed, cadence, power, altitude_m. start_s/end_s schneiden einen Abschnitt aus.
    Max. 2000 Punkte; für lange Einheiten resolution_s erhöhen.
    """
    return _safe(queries.timeseries, activity_id, resolution_s, fields, start_s, end_s)


@server.tool(annotations=READ_ONLY)
def daily_metrics(start_date: str | None = None, end_date: str | None = None) -> list | dict:
    """Tageswerte: HRV (Nacht, 7 Tage, Status, Basisbereich), Ruhepuls, Schlaf (Dauer, Tief, REM,
    Schlafwert), Body Battery, Stress, Schritte, Trainingsbereitschaft, Trainingsstatus mit akuter
    und chronischer Last, VO2max. Standard: letzte 28 Tage. Datum im Format YYYY-MM-DD."""
    return _safe(queries.daily_metrics, start_date, end_date)


@server.tool(annotations=READ_ONLY)
def weekly_summary(weeks: int = 12, sport: str | None = None) -> list | dict:
    """Wochenumfänge pro Sportart: Einheiten, km, Stunden, Trainingslast, Höhenmeter, Schnitt-Puls."""
    return _safe(queries.weekly_summary, weeks, sport)


@server.tool(annotations=READ_ONLY)
def describe_schema() -> dict:
    """Tabellen und Spalten der Datenbank, als Grundlage für query_sql."""
    return _safe(queries.schema)


@server.tool(annotations=READ_ONLY)
def query_sql(sql: str) -> dict:
    """Freie, rein lesende SQL-Abfrage (SQLite, nur SELECT/WITH, max. 2000 Zeilen, 15 s).

    Wichtige Tabellen: fit_sessions (pro Einheit), fit_laps (pro Runde), fit_records
    (Sekundenwerte, t = Sekunden seit Start), activities, daily_raw (Tageswerte als JSON,
    mit json_extract auslesbar). Erst describe_schema aufrufen, wenn Spalten unklar sind.
    """
    return _safe(queries.run_sql, sql)


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
    log.info("MCP-Server startet auf Port %d (Pfad %s)", PORT, PATH)
    # Kein Zugriffsprotokoll pro Anfrage; die Werkzeugaufrufe protokollieren wir selbst.
    config = uvicorn.Config(app, host="0.0.0.0", port=PORT, access_log=False, log_level="warning")
    await uvicorn.Server(config).serve()
