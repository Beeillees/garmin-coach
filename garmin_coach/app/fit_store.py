"""Speichert ausgewertete FIT-Dateien in SQLite und arbeitet offene Dateien ab."""

import json
import logging
import time
from pathlib import Path

import fit_parse
import metrics
from db import Store, now_iso

log = logging.getLogger("garmin_coach.fit")

# Erhöhen, wenn sich Parser oder Kennzahlen ändern: dann werden alle Dateien neu ausgewertet.
PARSER_VERSION = 1
# Erhöhen, wenn ein Fehler behoben wurde: dann werden nur die fehlerhaften Dateien erneut versucht.
ERROR_RETRY_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS fit_sessions (
    activity_id INTEGER PRIMARY KEY,
    parser_version INTEGER NOT NULL,
    parsed_at TEXT NOT NULL,
    sport TEXT, sub_sport TEXT, start_time TEXT,
    distance_m REAL, timer_s REAL, elapsed_s REAL,
    avg_speed REAL, max_speed REAL,
    avg_hr REAL, max_hr REAL, min_hr REAL,
    avg_cadence REAL, avg_power REAL, normalized_power REAL,
    avg_step_length_mm REAL, avg_gct_ms REAL, avg_vo_mm REAL, avg_vr_pct REAL,
    avg_resp REAL, ascent_m REAL, descent_m REAL, avg_temp REAL, calories REAL,
    te_aerobic REAL, te_anaerobic REAL, training_load REAL, rpe REAL, feel REAL,
    threshold_hr REAL, resting_hr REAL,
    hr_zone_bounds TEXT, time_in_hr_zone TEXT, devices TEXT,
    efficiency REAL, decoupling_pct REAL, speed_cv_pct REAL,
    target_hit_pct REAL, target_laps INTEGER, hr_recovery_60s REAL, hr_samples INTEGER,
    data TEXT
);
CREATE INDEX IF NOT EXISTS idx_fit_sessions_start ON fit_sessions(start_time);
CREATE INDEX IF NOT EXISTS idx_fit_sessions_sport ON fit_sessions(sport, start_time);

CREATE TABLE IF NOT EXISTS fit_laps (
    activity_id INTEGER NOT NULL,
    lap_index INTEGER NOT NULL,
    start_time TEXT, start_t INTEGER,
    intensity TEXT, lap_trigger TEXT, wkt_step_index INTEGER,
    target_speed_low REAL, target_speed_high REAL,
    distance_m REAL, timer_s REAL, elapsed_s REAL,
    avg_speed REAL, max_speed REAL, avg_hr REAL, max_hr REAL,
    avg_cadence REAL, max_cadence REAL,
    avg_step_length_mm REAL, avg_gct_ms REAL, gct_balance_pct REAL,
    avg_vo_mm REAL, avg_vr_pct REAL,
    avg_power REAL, max_power REAL, normalized_power REAL,
    avg_resp REAL, ascent_m REAL, descent_m REAL, avg_temp REAL, calories REAL,
    data TEXT,
    PRIMARY KEY (activity_id, lap_index)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS fit_records (
    activity_id INTEGER NOT NULL,
    t INTEGER NOT NULL,                 -- Sekunden seit Start der Einheit
    hr INTEGER, speed REAL, distance_m REAL, altitude_m REAL,
    cadence REAL, power INTEGER,
    gct_ms REAL, gct_balance_pct REAL, vo_mm REAL, vr_pct REAL, step_length_mm REAL,
    resp REAL, temp REAL,
    PRIMARY KEY (activity_id, t)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS fit_workout_steps (
    activity_id INTEGER NOT NULL,
    step_index INTEGER NOT NULL,
    intensity TEXT, duration_type TEXT, duration_value REAL, target_type TEXT,
    target_speed_low REAL, target_speed_high REAL, target_hr_low REAL, target_hr_high REAL,
    repeat_steps INTEGER, data TEXT,
    PRIMARY KEY (activity_id, step_index)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS fit_lengths (
    activity_id INTEGER NOT NULL,
    length_index INTEGER NOT NULL,
    start_time TEXT, length_type TEXT, swim_stroke TEXT,
    timer_s REAL, strokes INTEGER, cadence REAL, avg_speed REAL,
    PRIMARY KEY (activity_id, length_index)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS fit_sets (
    activity_id INTEGER NOT NULL,
    set_index INTEGER NOT NULL,
    start_time TEXT, set_type TEXT, duration_s REAL, repetitions INTEGER, weight REAL,
    data TEXT,
    PRIMARY KEY (activity_id, set_index)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS fit_errors (
    activity_id INTEGER PRIMARY KEY,
    parser_version INTEGER NOT NULL,
    failed_at TEXT NOT NULL,
    error TEXT
);
"""

CHILD_TABLES = ("fit_laps", "fit_records", "fit_workout_steps", "fit_lengths", "fit_sets")


def init(store: Store) -> None:
    store.conn.executescript(SCHEMA)
    store.conn.commit()


def _insert(conn, table: str, rows: list, activity_id: int) -> None:
    if not rows:
        return
    cols = ["activity_id"] + list(rows[0].keys())
    sql = f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})"
    conn.executemany(sql, [
        [activity_id] + [json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
                         for v in r.values()]
        for r in rows
    ])


def save(store: Store, activity_id: int, parsed: dict, m: dict) -> None:
    conn = store.conn
    with conn:  # eine Transaktion pro Datei
        for table in CHILD_TABLES:
            conn.execute(f"DELETE FROM {table} WHERE activity_id=?", (activity_id,))
        session = {"parser_version": PARSER_VERSION, "parsed_at": now_iso(), **parsed["session"], **m}
        _insert(conn, "fit_sessions", [session], activity_id)
        _insert(conn, "fit_laps", parsed["laps"], activity_id)
        _insert(conn, "fit_records", parsed["records"], activity_id)
        _insert(conn, "fit_workout_steps", parsed["workout_steps"], activity_id)
        _insert(conn, "fit_lengths", parsed["lengths"], activity_id)
        _insert(conn, "fit_sets", parsed["sets"], activity_id)
        conn.execute("DELETE FROM fit_errors WHERE activity_id=?", (activity_id,))


def _describe(parsed: dict, m: dict) -> str:
    s = parsed["session"]
    parts = [f"{s['sport']}", f"{(s.get('timer_s') or 0) / 60:.0f} min"]
    if s.get("distance_m"):
        parts.append(f"{s['distance_m'] / 1000:.2f} km")
    if s.get("avg_hr"):
        parts.append(f"HF {s['avg_hr']:.0f}/{s.get('max_hr') or 0:.0f}")
    parts.append(f"{len(parsed['laps'])} Runden")
    parts.append(f"{len(parsed['records'])} Sekundenwerte")
    if m.get("decoupling_pct") is not None:
        parts.append(f"Drift {m['decoupling_pct']:+.1f} %")
    if m.get("target_hit_pct") is not None:
        parts.append(f"Zieltempo {m['target_hit_pct']:.0f} % von {m['target_laps']} Runden")
    if m.get("hr_recovery_60s") is not None:
        parts.append(f"Pulserholung {m['hr_recovery_60s']:.0f}/min")
    return " | ".join(parts)


def process_file(store: Store, activity_id: int, path: Path) -> bool:
    try:
        parsed = fit_parse.parse(str(path))
        m = metrics.compute(parsed)
        save(store, activity_id, parsed, m)
        log.info("  ausgewertet %s: %s", activity_id, _describe(parsed, m))
        return True
    except Exception as e:
        store.conn.execute(
            "INSERT OR REPLACE INTO fit_errors(activity_id, parser_version, failed_at, error) "
            "VALUES(?,?,?,?)", (activity_id, ERROR_RETRY_VERSION, now_iso(), repr(e)[:1000]),
        )
        store.conn.commit()
        log.warning("  FIT %s konnte nicht ausgewertet werden: %s", activity_id, e)
        return False


def process_pending(store: Store, data_dir: Path) -> int:
    """Wertet alle FIT-Dateien aus, die noch nicht (oder mit alter Version) ausgewertet sind."""
    rows = store.conn.execute(
        """
        SELECT a.activity_id, a.fit_path FROM activities a
        LEFT JOIN fit_sessions s ON s.activity_id = a.activity_id
        LEFT JOIN fit_errors e ON e.activity_id = a.activity_id
        WHERE a.fit_path IS NOT NULL
          AND (s.activity_id IS NULL OR s.parser_version < ?)
          AND (e.activity_id IS NULL OR e.parser_version < ?)
        ORDER BY a.start_local
        """,
        (PARSER_VERSION, ERROR_RETRY_VERSION),
    ).fetchall()
    if not rows:
        return 0
    log.info("FIT-Auswertung: %d Dateien offen", len(rows))
    done = 0
    started = time.monotonic()
    for i, (aid, rel) in enumerate(rows, 1):
        path = data_dir / rel
        if not path.exists():
            continue
        log.info("[%d/%d] %s", i, len(rows), rel)
        done += process_file(store, aid, path)
    log.info("FIT-Auswertung fertig: %d Dateien in %.0f s", done, time.monotonic() - started)
    return done
