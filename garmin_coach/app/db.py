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

-- Zustände aus Garmin Connect, die nicht an einen Tag gebunden sind (Laktatschwelle,
-- Wettkampfprognosen, Bestzeiten, Pulszonen, geplante Trainings). Ein Schnappschuss pro
-- Tag und Quelle; daraus entsteht mit der Zeit die Historie.
CREATE TABLE IF NOT EXISTS garmin_snapshots (
    day        TEXT NOT NULL,
    source     TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    data       TEXT,
    PRIMARY KEY (day, source)
);

-- Notizen zu Krankheit, Verletzung, Reise, Stress usw.
CREATE TABLE IF NOT EXISTS notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    day_from   TEXT NOT NULL,
    day_to     TEXT NOT NULL,
    category   TEXT NOT NULL,
    text       TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notes_days ON notes(day_from, day_to);

-- Protokoll der MCP-Werkzeugaufrufe (wird auf 90 Tage begrenzt)
CREATE TABLE IF NOT EXISTS mcp_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    at         TEXT NOT NULL,
    tool       TEXT NOT NULL,
    params     TEXT,
    duration_ms INTEGER,
    result_rows INTEGER,
    status     TEXT,
    error      TEXT
);
CREATE INDEX IF NOT EXISTS idx_mcp_log_at ON mcp_log(at);

-- Trainingsplan als Text; jede Änderung ist eine neue Zeile, die neueste gilt.
CREATE TABLE IF NOT EXISTS coach_plan (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    text       TEXT NOT NULL
);

-- Wettkämpfe
CREATE TABLE IF NOT EXISTS races (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    day         TEXT NOT NULL,          -- YYYY-MM-DD
    name        TEXT NOT NULL,
    sport       TEXT,                   -- laufen, trail, schwimmen, hyrox, ...
    distance_km REAL,
    priority    TEXT,                   -- A, B, C
    status      TEXT NOT NULL DEFAULT 'geplant',  -- geplant, abgesagt, erledigt
    note        TEXT,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_races_day ON races(day);

-- Berichte des Coaches (Morgen, Training, Abend, Woche, Monat); verhindert doppelte Auswertungen.
CREATE TABLE IF NOT EXISTS coach_reports (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,          -- morgen, training, abend, woche, monat
    day         TEXT NOT NULL,
    activity_id INTEGER,
    created_at  TEXT NOT NULL,
    summary     TEXT
);
CREATE INDEX IF NOT EXISTS idx_coach_reports_day ON coach_reports(day, kind);
CREATE UNIQUE INDEX IF NOT EXISTS idx_coach_reports_activity ON coach_reports(activity_id)
    WHERE activity_id IS NOT NULL;

-- Trainingstagebuch: was Garmin nicht misst (Beine, Energie, Infektzeichen, Schmerzen, Verpflegung).
CREATE TABLE IF NOT EXISTS diary (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    day             TEXT NOT NULL,          -- YYYY-MM-DD
    activity_id     INTEGER,                -- optional: Bezug auf eine Einheit
    beine           INTEGER,                -- 1 frisch ... 5 sehr schwer
    energie         INTEGER,                -- 1 leer ... 5 top
    infekt          TEXT,                   -- z. B. 'Halskratzen', 'Schnupfen'
    schmerz         TEXT,                   -- Körperstelle und Art, z. B. 'Gesäß links, ziehend'
    schmerz_staerke INTEGER,                -- 0 ... 10
    kh_g_pro_h      REAL,                   -- Kohlenhydrate pro Stunde (lange Einheiten)
    fluessigkeit_ml_pro_h REAL,
    text            TEXT,                   -- Zusammenfassung
    original        TEXT,                   -- Brunos Eingabe im Wortlaut
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_diary_day ON diary(day);

-- Wetter je Aktivität (nur draußen, mit Startkoordinaten): Garmin-Wetterstation zum Start plus
-- Open-Meteo-Stundenwerte über die Dauer der Einheit. Abgeleitete Spalten in °C, km/h, mm.
CREATE TABLE IF NOT EXISTS activity_weather (
    activity_id     INTEGER PRIMARY KEY,
    fetched_at      TEXT NOT NULL,
    status          TEXT NOT NULL,          -- ok, teilweise, fehler
    temp_c          REAL,                   -- Mittel über die Einheit
    temp_min_c      REAL,
    temp_max_c      REAL,
    gefuehlt_c      REAL,
    feuchte_pct     REAL,
    wind_kmh        REAL,
    boeen_kmh       REAL,
    niederschlag_mm REAL,                   -- Summe während der Einheit
    schnee_cm       REAL,
    wetter          TEXT,                   -- Wetterlage als Text (z. B. 'Regen', 'bedeckt')
    extrem          TEXT,                   -- JSON-Liste, z. B. ["Frost", "Regen"]
    station         TEXT,                   -- Garmin-Wetterstation
    garmin          TEXT,                   -- Rohdaten Garmin (JSON)
    openmeteo       TEXT,                   -- Rohdaten Open-Meteo, nur die Stunden der Einheit (JSON)
    error           TEXT
);

-- Bewertung je Einheit gegen die Vorgabe des Tages (Brunos 30/40/30-Regel). Eigene Tabelle, damit der
-- Sync sie nie überschreibt. Ausgefallene Schlüsseleinheiten ohne Aktivität: activity_id NULL, day +
-- einheit gesetzt. Nur bewerten, wo es eine Vorgabe gab (ab 01.11.2026) – ältere Einheiten bleiben leer.
CREATE TABLE IF NOT EXISTS activity_rating (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_id INTEGER,                -- NULL = ausgefallene Einheit ohne Aufzeichnung
    day         TEXT NOT NULL,          -- YYYY-MM-DD
    einheit     TEXT,                   -- Kurzname, z. B. 'Schwelle 4×10', 'Parkrun'
    bewertung   TEXT NOT NULL,          -- uebererfuellt, erfuellt, gekuerzt, verfehlt
    schluessel  INTEGER NOT NULL DEFAULT 1,  -- 1 = Schlüsseleinheit (zählt für die Quote)
    grund       TEXT,                   -- kurze Begründung gegen die Vorgabe
    quelle      TEXT NOT NULL DEFAULT 'coach',  -- coach, bruno, nachtrag
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_rating_activity ON activity_rating(activity_id)
    WHERE activity_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_rating_day_unit ON activity_rating(day, einheit)
    WHERE activity_id IS NULL;
CREATE INDEX IF NOT EXISTS idx_rating_day ON activity_rating(day);
"""

# Rückwirkende Bewertungen (aus den Coach-Berichten ab 01.11.2026, mit Bruno besprochen am 25.11.2026).
RATING_SEED_FILE = Path(__file__).with_name("bewertung_nachtrag.json")

# Spalten, die nach der ersten Version dazugekommen sind: (Tabelle, Spalte, Definition)
MIGRATIONS = [
    ("sync_log", "kind", "TEXT DEFAULT 'voll'"),
    ("coach_reports", "period", "TEXT"),       # 0.6: '2026-W44' bzw. '2026-10' für Wochen-/Monatsberichte
    ("diary", "original", "TEXT"),             # 0.8: Brunos Eingabe im Wortlaut (text = Zusammenfassung)
    # 0.11: strukturiertes Tagebuch
    ("diary", "zeitpunkt", "TEXT"),            # morgens, nach_einheit, tagsueber, abends
    ("diary", "motivation", "INTEGER"),        # 1 null Bock ... 5 heiß drauf
    ("diary", "infekt_grad", "INTEGER"),       # 0 keiner, 1 leicht, 2 deutlich, 3 krank
    ("diary", "schmerzen", "TEXT"),            # JSON-Liste [{ort, seite, staerke, art}]
    ("diary", "kontext", "TEXT"),              # JSON-Liste fester Stichworte (sauna, reise, nass, ...)
    ("diary", "kh_g", "REAL"),                 # Kohlenhydrate gesamt während der Einheit
    ("diary", "fluessigkeit_ml", "REAL"),      # getrunken gesamt während der Einheit
    ("diary", "stockwerke_geraet", "INTEGER"), # Treppe: Stockwerke laut Gerät im Studio
    ("coach_reports", "feedback", "TEXT"),     # 0.11: Brunos Rückmeldung zum Bericht
]

# Indizes, die erst nach den Migrationen angelegt werden können
POST_MIGRATION = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_coach_reports_period ON coach_reports(kind, period)
    WHERE period IS NOT NULL;
"""


def zeitpunkt_aus(day: str, activity_id, created_at: str):
    """Zeitpunkt eines Tagebucheintrags ableiten, wenn er nicht angegeben ist (sonst None)."""
    if activity_id:
        return "nach_einheit"
    try:
        local = datetime.fromisoformat(created_at).astimezone()
    except (TypeError, ValueError):
        return None
    if local.date().isoformat() != (day or "")[:10]:
        return None  # nachgetragen: Uhrzeit sagt nichts
    if local.hour < 10:
        return "morgens"
    if local.hour >= 18:
        return "abends"
    return "tagsueber"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), timeout=30)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        for table, col, definition in MIGRATIONS:
            cols = {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            if col not in cols:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")
        self.conn.executescript(POST_MIGRATION)
        self._backfill_diary_zeitpunkt()
        self._seed_ratings()
        self.conn.commit()

    def _seed_ratings(self) -> None:
        """Rückwirkende Bewertungen einmalig eintragen. Vorhandene Einträge (z. B. von Bruno
        korrigiert) werden nie überschrieben."""
        if not RATING_SEED_FILE.exists():
            return
        try:
            seed = json.loads(RATING_SEED_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        now = now_iso()
        for r in seed.get("bewertungen", []):
            self.conn.execute(
                "INSERT OR IGNORE INTO activity_rating(activity_id, day, einheit, bewertung, schluessel, "
                "grund, quelle, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (r.get("activity_id"), r["day"], r.get("einheit"), r["bewertung"],
                 1 if r.get("schluessel", True) else 0, r.get("grund"), "nachtrag", now, now))

    def _backfill_diary_zeitpunkt(self) -> None:
        """Alte Tagebucheinträge (vor 0.11) bekommen einen Zeitpunkt: mit Einheit 'nach_einheit',
        sonst aus der lokalen Uhrzeit des Eintrags, wenn er am selben Tag geschrieben wurde."""
        rows = self.conn.execute(
            "SELECT id, day, activity_id, created_at FROM diary WHERE zeitpunkt IS NULL").fetchall()
        for rid, day, activity_id, created_at in rows:
            zp = zeitpunkt_aus(day, activity_id, created_at)
            if zp:
                self.conn.execute("UPDATE diary SET zeitpunkt=? WHERE id=?", (zp, rid))

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

    def save_snapshot(self, day: str, source: str, data) -> None:
        payload = None if data in (None, {}, []) else json.dumps(data, ensure_ascii=False)
        self.conn.execute(
            "INSERT INTO garmin_snapshots(day, source, fetched_at, data) VALUES(?,?,?,?) "
            "ON CONFLICT(day, source) DO UPDATE SET fetched_at=excluded.fetched_at, "
            "data=COALESCE(excluded.data, garmin_snapshots.data)",
            (day, source, now_iso(), payload),
        )
        self.conn.commit()

    # --- Aktivitäten --------------------------------------------------------

    def has_activity(self, activity_id: int) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM activities WHERE activity_id=?", (activity_id,)
        ).fetchone() is not None

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

    def log_start(self, kind: str = "voll") -> int:
        cur = self.conn.execute(
            "INSERT INTO sync_log(started_at, status, kind) VALUES(?, 'running', ?)", (now_iso(), kind)
        )
        self.conn.commit()
        return cur.lastrowid

    def close_orphaned_syncs(self) -> int:
        """Beim Start: Läufe, die noch auf 'running' stehen, wurden durch einen Neustart abgebrochen."""
        n = self.conn.execute(
            "UPDATE sync_log SET status='abgebrochen', message=COALESCE(message, 'durch Neustart abgebrochen') "
            "WHERE status='running'"
        ).rowcount
        self.conn.commit()
        return n

    def log_finish(self, log_id: int, status: str, message: str) -> None:
        self.conn.execute(
            "UPDATE sync_log SET finished_at=?, status=?, message=? WHERE id=?",
            (now_iso(), status, message[:2000], log_id),
        )
        self.conn.commit()
