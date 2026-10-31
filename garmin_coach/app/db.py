"""SQLite-Speicher für Garmin-Rohdaten.

Rohdaten werden unverändert als JSON abgelegt. So geht nichts verloren, auch
wenn Garmin Felder umbenennt; Auswertungen bauen später darauf auf.
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_raw (
    day        TEXT NOT NULL,          -- YYYY-MM-DD
    source     TEXT NOT NULL,          -- z. B. 'hrv', 'sleep', 'user_summary'
    fetched_at TEXT NOT NULL,
    data       TEXT,                   -- JSON oder NULL, wenn Garmin nichts liefert
    PRIMARY KEY (day, source)
);

CREATE TABLE IF NOT EXISTS activities (
    activity_id  INTEGER PRIMARY KEY,
    start_local  TEXT,
    type_key     TEXT,
    name         TEXT,
    distance_m   REAL,
    duration_s   REAL,
    avg_hr       REAL,
    max_hr       REAL,
    fit_path     TEXT,                 -- relativer Pfad unter dem Datenordner
    fetched_at   TEXT NOT NULL,
    data         TEXT NOT NULL         -- komplette Aktivitäts-Zusammenfassung als JSON
);

CREATE INDEX IF NOT EXISTS idx_activities_start ON activities(start_local);

CREATE TABLE IF NOT EXISTS sync_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status     TEXT,
    message    TEXT
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # --- Tageswerte ---------------------------------------------------------

    def has_daily(self, day: str, source: str) -> bool:
        row = self.conn.execute(
            "SELECT data FROM daily_raw WHERE day=? AND source=?", (day, source)
        ).fetchone()
        return row is not None and row[0] is not None

    def save_daily(self, day: str, source: str, data) -> None:
        payload = None if data in (None, {}, []) else json.dumps(data, ensure_ascii=False)
        self.conn.execute(
            "INSERT INTO daily_raw(day, source, fetched_at, data) VALUES(?,?,?,?) "
            "ON CONFLICT(day, source) DO UPDATE SET fetched_at=excluded.fetched_at, "
            "data=COALESCE(excluded.data, daily_raw.data)",
            (day, source, now_iso(), payload),
        )
        self.conn.commit()

    # --- Aktivitäten --------------------------------------------------------

    def activity_fit_path(self, activity_id: int):
        row = self.conn.execute(
            "SELECT fit_path FROM activities WHERE activity_id=?", (activity_id,)
        ).fetchone()
        return None if row is None else row[0]

    def save_activity(self, act: dict, fit_path) -> None:
        type_key = (act.get("activityType") or {}).get("typeKey")
        self.conn.execute(
            "INSERT INTO activities(activity_id, start_local, type_key, name, distance_m, "
            "duration_s, avg_hr, max_hr, fit_path, fetched_at, data) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(activity_id) DO UPDATE SET start_local=excluded.start_local, "
            "type_key=excluded.type_key, name=excluded.name, distance_m=excluded.distance_m, "
            "duration_s=excluded.duration_s, avg_hr=excluded.avg_hr, max_hr=excluded.max_hr, "
            "fit_path=COALESCE(excluded.fit_path, activities.fit_path), "
            "fetched_at=excluded.fetched_at, data=excluded.data",
            (
                int(act["activityId"]),
                act.get("startTimeLocal"),
                type_key,
                act.get("activityName"),
                act.get("distance"),
                act.get("duration"),
                act.get("averageHR"),
                act.get("maxHR"),
                fit_path,
                now_iso(),
                json.dumps(act, ensure_ascii=False),
            ),
        )
        self.conn.commit()

    # --- Protokoll ----------------------------------------------------------

    def log_start(self) -> int:
        cur = self.conn.execute(
            "INSERT INTO sync_log(started_at, status) VALUES(?, 'running')", (now_iso(),)
        )
        self.conn.commit()
        return cur.lastrowid

    def log_finish(self, log_id: int, status: str, message: str) -> None:
        self.conn.execute(
            "UPDATE sync_log SET finished_at=?, status=?, message=? WHERE id=?",
            (now_iso(), status, message[:2000], log_id),
        )
        self.conn.commit()
