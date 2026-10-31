"""Lese-Abfragen auf garmin.db für den MCP-Server.

Alle Funktionen geben JSON-taugliche Dicts in gut lesbaren Einheiten zurück
(km, min/km, Minuten, Schritte/min). Gelesen wird über schreibgeschützte
Verbindungen; nur die Notizen werden über eine eigene Verbindung geschrieben.
"""

import json
import sqlite3
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import sports

DB_FILE = Path("/share/garmin-coach/garmin.db")

MAX_ROWS = 2000
QUERY_TIMEOUT_S = 15

NOTE_CATEGORIES = ("krank", "verletzt", "reise", "stress", "sonstiges")


def _sport_group_sql(type_key, fit_sport=None):
    return sports.group(type_key, fit_sport)


@contextmanager
def connect(db_file: Path = None):
    path = Path(db_file or DB_FILE)
    if not path.exists():
        raise FileNotFoundError(f"Datenbank {path} existiert noch nicht")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    # sport_gruppe(type_key [, fit_sport]) steht auch in query_sql zur Verfügung
    conn.create_function("sport_gruppe", 1, _sport_group_sql, deterministic=True)
    conn.create_function("sport_gruppe", 2, _sport_group_sql, deterministic=True)
    deadline = time.monotonic() + QUERY_TIMEOUT_S
    conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10000)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def connect_rw(db_file: Path = None):
    """Schreibende Verbindung für Notizen, Plan, Wettkämpfe und Berichte."""
    path = Path(db_file or DB_FILE)
    if not path.exists():
        raise FileNotFoundError(f"Datenbank {path} existiert noch nicht")
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _table_exists(c, name: str) -> bool:
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


# --- Formatierung -------------------------------------------------------------

def pace(speed):
    """m/s -> 'm:ss' pro km."""
    if not speed or speed <= 0.3:
        return None
    s = int(round(1000 / speed))
    return f"{s // 60}:{s % 60:02d}"


def _r(v, n=1):
    return None if v is None else round(v, n)


def _km(m):
    return None if m is None else round(m / 1000, 2)


def _min(s):
    return None if s is None else round(s / 60, 1)


def _h(minutes):
    return None if minutes is None else round(minutes / 60, 1)


def _loads(v):
    if v is None:
        return None
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return v


def _clean(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None and v != [] and v != {}}


def _first_not_none(*vals):
    for v in vals:
        if v is not None:
            return v
    return None


def _local_clock(ms):
    """Garmins 'Local'-Zeitstempel (ms, lokale Zeit als Epoche) -> 'HH:MM'."""
    if not ms:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%H:%M")
    except (TypeError, ValueError, OverflowError):
        return None


def _next_day(d: str) -> str:
    return (date.fromisoformat(d[:10]) + timedelta(days=1)).isoformat()


def _iso_week(day: str) -> str:
    y, w, _ = date.fromisoformat(day[:10]).isocalendar()
    return f"{y}-W{w:02d}"


# --- Notizen ------------------------------------------------------------------

def _notes_between(c, start: str, end: str) -> list:
    if not _table_exists(c, "notes"):
        return []
    return [dict(r) for r in c.execute(
        "SELECT id, day_from, day_to, category, text FROM notes "
        "WHERE day_from <= ? AND day_to >= ? ORDER BY day_from, id", (end, start))]


def _note_text(n: dict) -> str:
    return f"{n['category']}: {n['text']}"


def add_note(day_from: str, text: str, category: str = "sonstiges", day_to: str = None, db_file=None) -> dict:
    category = (category or "sonstiges").strip().lower()
    if category not in NOTE_CATEGORIES:
        raise ValueError(f"Kategorie muss eine von {', '.join(NOTE_CATEGORIES)} sein")
    start = date.fromisoformat(day_from[:10]).isoformat()
    end = date.fromisoformat((day_to or day_from)[:10]).isoformat()
    if end < start:
        raise ValueError("day_to liegt vor day_from")
    if not (text or "").strip():
        raise ValueError("Text darf nicht leer sein")
    with connect_rw(db_file) as c:
        cur = c.execute(
            "INSERT INTO notes(day_from, day_to, category, text, created_at) VALUES(?,?,?,?,?)",
            (start, end, category, text.strip()[:2000],
             datetime.now(timezone.utc).isoformat(timespec="seconds")))
        note_id = cur.lastrowid
    return {"id": note_id, "von": start, "bis": end, "kategorie": category, "text": text.strip()}


def list_notes(start_date=None, end_date=None, db_file=None) -> list:
    end = end_date or "9999-12-31"
    start = start_date or "0000-01-01"
    with connect(db_file) as c:
        notes = _notes_between(c, start, end)
    return [{"id": n["id"], "von": n["day_from"], "bis": n["day_to"], "kategorie": n["category"],
             "text": n["text"]} for n in notes]


def delete_note(note_id: int, db_file=None) -> dict:
    with connect_rw(db_file) as c:
        n = c.execute("DELETE FROM notes WHERE id=?", (int(note_id),)).rowcount
    if not n:
        raise ValueError(f"Notiz {note_id} nicht gefunden")
    return {"geloescht": int(note_id)}


# --- Überblick ----------------------------------------------------------------

def overview(db_file=None) -> dict:
    with connect(db_file) as c:
        one = lambda sql: c.execute(sql).fetchone()
        daily = one("SELECT MIN(day), MAX(day), COUNT(DISTINCT day) FROM daily_raw WHERE data IS NOT NULL")
        acts = one("SELECT MIN(start_local), MAX(start_local), COUNT(*), SUM(fit_path IS NOT NULL) FROM activities")
        parsed = one("SELECT COUNT(*), MAX(a.start_local) FROM fit_sessions s "
                     "JOIN activities a USING(activity_id)")
        errors = one("SELECT COUNT(*) FROM fit_errors")[0]
        rows = c.execute(
            "SELECT a.type_key, s.sport, COALESCE(s.distance_m, a.distance_m) AS dist, "
            "COALESCE(s.timer_s, a.duration_s) AS dur FROM activities a "
            "LEFT JOIN fit_sessions s USING(activity_id)").fetchall()
        last_sync = one("SELECT started_at, finished_at, status, message FROM sync_log ORDER BY id DESC LIMIT 1")
        n_notes = one("SELECT COUNT(*) FROM notes")[0] if _table_exists(c, "notes") else 0
        snaps = (one("SELECT MAX(day), COUNT(DISTINCT source) FROM garmin_snapshots WHERE data IS NOT NULL")
                 if _table_exists(c, "garmin_snapshots") else (None, 0))
    groups = {}
    for r in rows:
        g = sports.group(r["type_key"], r["sport"])
        e = groups.setdefault(g, {"sport_gruppe": g, "anzahl": 0, "km": 0.0, "stunden": 0.0, "typen": {}})
        e["anzahl"] += 1
        e["km"] += (r["dist"] or 0) / 1000
        e["stunden"] += (r["dur"] or 0) / 3600
        tk = r["type_key"] or r["sport"] or "?"
        e["typen"][tk] = e["typen"].get(tk, 0) + 1
    sport_list = sorted(groups.values(), key=lambda e: -e["anzahl"])
    for e in sport_list:
        e["km"], e["stunden"] = round(e["km"], 1), round(e["stunden"], 1)
    total = acts[2] or 0
    return {
        "tageswerte": {"von": daily[0], "bis": daily[1], "tage": daily[2]},
        "aktivitaeten": {"von": acts[0], "bis": acts[1], "anzahl": total, "mit_fit": acts[3]},
        "fit_auswertung": {
            "ausgewertet": parsed[0], "gesamt": acts[3], "offen": max((acts[3] or 0) - parsed[0] - errors, 0),
            "fehlerhaft": errors, "ausgewertet_bis": parsed[1],
            "fortschritt_pct": round(100 * parsed[0] / acts[3], 1) if acts[3] else None,
        },
        "sportarten": sport_list,
        "hinweis_sportarten": "Über alle Aktivitäten; nicht ausgewertete Einheiten mit Werten aus Garmin Connect.",
        "notizen": n_notes,
        "schnappschuesse": {"letzter_tag": snaps[0], "quellen": snaps[1]},
        "letzter_sync": dict(last_sync) if last_sync else None,
    }


# --- Aktivitäten --------------------------------------------------------------

def _cadence_from_json(js: dict, group: str):
    if group == "laufen":
        return js.get("averageRunningCadenceInStepsPerMinute")
    if group == "rad":
        return js.get("averageBikingCadenceInRevPerMinute")
    if group == "schwimmen":
        return js.get("averageSwimCadenceInStrokesPerMinute")
    return None


def _zones_from_json(js: dict, prefix: str):
    vals = [js.get(f"{prefix}_{i}") for i in range(1, 6)]
    return None if all(v is None for v in vals) else [_min(v or 0) for v in vals]


def _fit_zones_1_5(raw):
    """FIT speichert [unter Zone 1, Zone 1..5, über Max]; wir geben wie Garmin Zone 1..5 aus."""
    z = _loads(raw)
    if not isinstance(z, list) or not z:
        return None
    zones = z[1:6] if len(z) >= 6 else z
    return [_min(x or 0) for x in zones]


def _merged(r, js: dict) -> dict:
    """Werte einer Einheit: zuerst FIT-Auswertung, sonst Garmin Connect."""
    g = sports.group(r["type_key"], r["sport"])
    has_fit = r["sport"] is not None
    sl_mm = _first_not_none(r["avg_step_length_mm"],
                            js.get("avgStrideLength") and js["avgStrideLength"] * 10)
    vo_mm = _first_not_none(r["avg_vo_mm"],
                            js.get("avgVerticalOscillation") and js["avgVerticalOscillation"] * 10)
    zones = _fit_zones_1_5(r["time_in_hr_zone"]) if has_fit else None
    return {
        "group": g, "has_fit": has_fit,
        "distance_m": _first_not_none(r["s_distance_m"], js.get("distance"), r["a_distance_m"]),
        "timer_s": _first_not_none(r["timer_s"], js.get("movingDuration"), js.get("duration"), r["duration_s"]),
        "avg_speed": _first_not_none(r["avg_speed"], js.get("averageSpeed")),
        "gap_speed": js.get("avgGradeAdjustedSpeed"),
        "avg_hr": _first_not_none(r["s_avg_hr"], js.get("averageHR"), r["a_avg_hr"]),
        "max_hr": _first_not_none(r["s_max_hr"], js.get("maxHR"), r["a_max_hr"]),
        "cadence": _first_not_none(r["avg_cadence"], _cadence_from_json(js, g)),
        "power": _first_not_none(r["avg_power"], js.get("avgPower")),
        "np": _first_not_none(r["normalized_power"], js.get("normPower")),
        "ascent": _first_not_none(r["ascent_m"], js.get("elevationGain")),
        "descent": _first_not_none(r["descent_m"], js.get("elevationLoss")),
        "te_aerobic": _first_not_none(r["te_aerobic"], js.get("aerobicTrainingEffect")),
        "te_anaerobic": _first_not_none(r["te_anaerobic"], js.get("anaerobicTrainingEffect")),
        "load": _first_not_none(r["training_load"], js.get("activityTrainingLoad")),
        "gct": _first_not_none(r["avg_gct_ms"], js.get("avgGroundContactTime")),
        "gct_balance": js.get("avgGroundContactBalance"),
        "vo_mm": vo_mm, "vr_pct": _first_not_none(r["avg_vr_pct"], js.get("avgVerticalRatio")),
        "step_length_mm": sl_mm,
        "resp": _first_not_none(r["avg_resp"], js.get("avgRespirationRate")),
        "hr_zones_min": zones or _zones_from_json(js, "hrTimeInZone"),
        "power_zones_min": _zones_from_json(js, "powerTimeInZone"),
    }


_LIST_COLS = """
    a.activity_id, a.start_local, a.name, a.type_key, a.data AS a_data,
    a.distance_m AS a_distance_m, a.duration_s, a.avg_hr AS a_avg_hr, a.max_hr AS a_max_hr,
    s.sport, s.sub_sport, s.distance_m AS s_distance_m, s.timer_s, s.avg_speed,
    s.avg_hr AS s_avg_hr, s.max_hr AS s_max_hr, s.avg_cadence, s.avg_power, s.normalized_power,
    s.ascent_m, s.descent_m, s.te_aerobic, s.te_anaerobic, s.training_load,
    s.avg_step_length_mm, s.avg_gct_ms, s.avg_vo_mm, s.avg_vr_pct, s.avg_resp, s.time_in_hr_zone,
    s.rpe, s.feel, s.efficiency, s.decoupling_pct, s.speed_cv_pct, s.target_hit_pct, s.hr_recovery_60s
"""


def list_activities(start_date=None, end_date=None, sport=None, limit=50, db_file=None) -> list:
    where, args = ["1=1"], []
    if start_date:
        where.append("a.start_local >= ?"); args.append(start_date)
    if end_date:
        where.append("a.start_local < ?"); args.append(_next_day(end_date))
    if sport:
        where.append("(sport_gruppe(a.type_key, s.sport) = ? OR a.type_key = ? OR s.sport = ?)")
        args += [sport.strip().lower()] * 3
    sql = f"""SELECT {_LIST_COLS}
        FROM activities a LEFT JOIN fit_sessions s ON s.activity_id = a.activity_id
        WHERE {' AND '.join(where)}
        ORDER BY a.start_local DESC LIMIT ?"""
    args.append(min(int(limit or 50), 500))
    with connect(db_file) as c:
        rows = c.execute(sql, args).fetchall()
    out = []
    for r in rows:
        js = _loads(r["a_data"]) or {}
        m = _merged(r, js)
        run = m["group"] == "laufen"
        out.append(_clean({
            "activity_id": r["activity_id"],
            "start": r["start_local"],
            "name": r["name"],
            "sport_gruppe": m["group"],
            "garmin_typ": r["type_key"],
            "sub_sport": r["sub_sport"],
            "quelle": "fit" if m["has_fit"] else "garmin_json",
            "km": _km(m["distance_m"]),
            "minuten": _min(m["timer_s"]),
            "pace": pace(m["avg_speed"]) if run else None,
            "pace_gap": pace(m["gap_speed"]) if run else None,
            "hf_avg": m["avg_hr"], "hf_max": m["max_hr"],
            "kadenz": _r(m["cadence"], 0), "watt": _r(m["power"], 0), "hoehenmeter": _r(m["ascent"], 0),
            "te_aerob": _r(m["te_aerobic"]), "te_anaerob": _r(m["te_anaerobic"]),
            "te_label": js.get("trainingEffectLabel"), "last": _r(m["load"], 0),
            "body_battery_diff": js.get("differenceBodyBattery"),
            "hf_zonen_min": m["hr_zones_min"],
            "bodenkontakt_ms": _r(m["gct"], 0) if run else None,
            "schrittlaenge_m": _r(m["step_length_mm"] and m["step_length_mm"] / 1000, 2) if run else None,
            "rpe": r["rpe"], "gefuehl": r["feel"],
            "effizienz": r["efficiency"], "drift_pct": r["decoupling_pct"], "tempo_cv_pct": r["speed_cv_pct"],
            "zieltempo_treffer_pct": r["target_hit_pct"], "pulserholung_60s": r["hr_recovery_60s"],
            "bestzeit": True if js.get("pr") or js.get("isPR") else None,
        }))
    return out


_FASTEST = {"1000": "1 km", "1609": "1 Meile", "5000": "5 km", "10000": "10 km",
            "21098": "Halbmarathon", "42195": "Marathon"}


def _garmin_summary(js: dict, group: str) -> dict:
    """Zusatzwerte aus Garmin Connect, die nicht in der FIT-Auswertung stehen."""
    run = group == "laufen"
    fastest = {label: _fmt_duration(js.get(f"fastestSplit_{k}")) for k, label in _FASTEST.items()
               if js.get(f"fastestSplit_{k}")}
    return _clean({
        "km": _km(js.get("distance")), "minuten_bewegung": _min(js.get("movingDuration")),
        "minuten_gesamt": _min(js.get("elapsedDuration") or js.get("duration")),
        "pace": pace(js.get("averageSpeed")) if run else None,
        "pace_gap": pace(js.get("avgGradeAdjustedSpeed")) if run else None,
        "hf_avg": js.get("averageHR"), "hf_max": js.get("maxHR"),
        "kadenz": _r(_cadence_from_json(js, group), 0),
        "watt": js.get("avgPower"), "watt_np": js.get("normPower"), "watt_max": js.get("maxPower"),
        "hoehenmeter_auf": _r(js.get("elevationGain"), 0), "hoehenmeter_ab": _r(js.get("elevationLoss"), 0),
        "te_aerob": js.get("aerobicTrainingEffect"), "te_anaerob": js.get("anaerobicTrainingEffect"),
        "te_label": js.get("trainingEffectLabel"),
        "te_nachricht": js.get("aerobicTrainingEffectMessage"),
        "trainingslast": _r(js.get("activityTrainingLoad"), 0),
        "body_battery_diff": js.get("differenceBodyBattery"),
        "vo2max_schaetzung": js.get("vO2MaxValue"),
        "bodenkontakt_ms": _r(js.get("avgGroundContactTime"), 0) if run else None,
        "gct_balance_links_pct": _r(js.get("avgGroundContactBalance")) if run else None,
        "vert_bewegung_cm": _r(js.get("avgVerticalOscillation")) if run else None,
        "vert_verhaeltnis_pct": _r(js.get("avgVerticalRatio")) if run else None,
        "schrittlaenge_m": _r(js.get("avgStrideLength") and js["avgStrideLength"] / 100, 2) if run else None,
        "atemfrequenz_avg": _r(js.get("avgRespirationRate")),
        "atemfrequenz_min": _r(js.get("minRespirationRate")), "atemfrequenz_max": _r(js.get("maxRespirationRate")),
        "temperatur_min": js.get("minTemperature"), "temperatur_max": js.get("maxTemperature"),
        "schweissverlust_ml": js.get("waterEstimated"),
        "kalorien": js.get("calories"),
        "intensitaetsminuten_moderat": js.get("moderateIntensityMinutes"),
        "intensitaetsminuten_intensiv": js.get("vigorousIntensityMinutes"),
        "hf_zonen_min": _zones_from_json(js, "hrTimeInZone"),
        "leistungszonen_min": _zones_from_json(js, "powerTimeInZone"),
        "schnellste_abschnitte": fastest,
        "bestzeit": True if js.get("pr") or js.get("isPR") else None,
        "ort": js.get("locationName"),
        "saetze": js.get("totalSets"), "wiederholungen": js.get("totalReps"),
        "bahnlaenge_m": _r(js.get("poolLength") and js["poolLength"] / 100, 1) if group == "schwimmen" else None,
    })


def _fmt_duration(s):
    if not s:
        return None
    s = int(round(s))
    h, rest = divmod(s, 3600)
    return f"{h}:{rest // 60:02d}:{rest % 60:02d}" if h else f"{rest // 60}:{rest % 60:02d}"


def get_activity(activity_id: int, db_file=None) -> dict:
    with connect(db_file) as c:
        a = c.execute("SELECT * FROM activities WHERE activity_id=?", (activity_id,)).fetchone()
        s = c.execute("SELECT * FROM fit_sessions WHERE activity_id=?", (activity_id,)).fetchone()
        laps = c.execute("SELECT * FROM fit_laps WHERE activity_id=? ORDER BY lap_index", (activity_id,)).fetchall()
        steps = c.execute("SELECT * FROM fit_workout_steps WHERE activity_id=? ORDER BY step_index", (activity_id,)).fetchall()
        lengths = c.execute("SELECT * FROM fit_lengths WHERE activity_id=? ORDER BY length_index", (activity_id,)).fetchall()
        sets = c.execute("SELECT * FROM fit_sets WHERE activity_id=? ORDER BY set_index", (activity_id,)).fetchall()
        err = c.execute("SELECT error FROM fit_errors WHERE activity_id=?", (activity_id,)).fetchone()
        notes = _notes_between(c, a["start_local"][:10], a["start_local"][:10]) if a and a["start_local"] else []
    if not a and not s:
        raise ValueError(f"Aktivität {activity_id} nicht gefunden")
    js = (_loads(a["data"]) if a else None) or {}
    group = sports.group(a["type_key"] if a else None, s["sport"] if s else None)
    out = {"activity_id": activity_id, "sport_gruppe": group}
    if a:
        out.update({"name": a["name"], "start_lokal": a["start_local"], "garmin_typ": a["type_key"]})
    if err:
        out["auswertungsfehler"] = err[0]
    if not s:
        out["hinweis"] = "FIT-Datei noch nicht ausgewertet; Werte unter 'garmin' stammen aus Garmin Connect."
    if s:
        out["einheit"] = {
            "sport": s["sport"], "sub_sport": s["sub_sport"], "start_utc": s["start_time"],
            "km": _km(s["distance_m"]), "minuten": _min(s["timer_s"]), "minuten_gesamt": _min(s["elapsed_s"]),
            "pace": pace(s["avg_speed"]), "pace_max": pace(s["max_speed"]),
            "hf_avg": s["avg_hr"], "hf_max": s["max_hr"], "hf_min": s["min_hr"],
            "kadenz": s["avg_cadence"], "watt": s["avg_power"], "watt_np": s["normalized_power"],
            "schrittlaenge_m": _r(s["avg_step_length_mm"] and s["avg_step_length_mm"] / 1000, 2),
            "bodenkontakt_ms": _r(s["avg_gct_ms"], 0), "vert_bewegung_cm": _r(s["avg_vo_mm"] and s["avg_vo_mm"] / 10),
            "vert_verhaeltnis_pct": _r(s["avg_vr_pct"]), "atemfrequenz": _r(s["avg_resp"]),
            "hoehenmeter_auf": s["ascent_m"], "hoehenmeter_ab": s["descent_m"], "temperatur": s["avg_temp"],
            "kalorien": s["calories"], "te_aerob": s["te_aerobic"], "te_anaerob": s["te_anaerobic"],
            "trainingslast": _r(s["training_load"], 0), "rpe": s["rpe"], "gefuehl": s["feel"],
            "schwellen_hf": s["threshold_hr"], "ruhe_hf": s["resting_hr"],
            "hf_zonen_obergrenzen": _loads(s["hr_zone_bounds"]),
            "zeit_in_hf_zonen_min": _fit_zones_1_5(s["time_in_hr_zone"]),
            "zeit_unter_zone1_min": _min((_loads(s["time_in_hr_zone"]) or [None])[0]),
            "geraete": _loads(s["devices"]),
            "effizienz": s["efficiency"], "drift_pct": s["decoupling_pct"], "tempo_cv_pct": s["speed_cv_pct"],
            "zieltempo_treffer_pct": s["target_hit_pct"], "zieltempo_runden": s["target_laps"],
            "pulserholung_60s": s["hr_recovery_60s"], "sekundenwerte_puls": s["hr_samples"],
        }
    if js:
        out["garmin"] = _garmin_summary(js, group)
    if notes:
        out["notizen"] = [_note_text(n) for n in notes]
    out["runden"] = [{
        "nr": l["lap_index"] + 1, "typ": l["intensity"] or None, "ausloeser": l["lap_trigger"],
        "start_s": l["start_t"], "workout_schritt": l["wkt_step_index"],
        "ziel_pace": (f"{pace(l['target_speed_high'])}-{pace(l['target_speed_low'])}"
                      if l["target_speed_low"] and l["target_speed_high"] else None),
        "km": _km(l["distance_m"]), "sekunden": _r(l["timer_s"], 0), "pace": pace(l["avg_speed"]),
        "hf_avg": l["avg_hr"], "hf_max": l["max_hr"], "kadenz": l["avg_cadence"],
        "schrittlaenge_m": _r(l["avg_step_length_mm"] and l["avg_step_length_mm"] / 1000, 2),
        "bodenkontakt_ms": _r(l["avg_gct_ms"], 0), "gct_balance_links_pct": _r(l["gct_balance_pct"]),
        "vert_bewegung_cm": _r(l["avg_vo_mm"] and l["avg_vo_mm"] / 10), "vert_verhaeltnis_pct": _r(l["avg_vr_pct"]),
        "watt": l["avg_power"], "watt_max": l["max_power"], "watt_np": l["normalized_power"],
        "atemfrequenz": _r(l["avg_resp"]), "hm_auf": l["ascent_m"], "hm_ab": l["descent_m"],
        "temperatur": l["avg_temp"],
    } for l in laps]
    if not laps and js.get("splitSummaries"):
        out["abschnitte_garmin"] = [_clean({
            "typ": sp.get("splitType"), "anzahl": sp.get("noOfSplits"), "km": _km(sp.get("distance")),
            "minuten": _min(sp.get("duration")), "pace": pace(sp.get("averageSpeed")) if group == "laufen" else None,
            "hf_avg": sp.get("averageHR"), "hf_max": sp.get("maxHR"),
        }) for sp in js["splitSummaries"] if isinstance(sp, dict)]
    if steps:
        out["workout_schritte"] = [{
            "index": st["step_index"], "typ": st["intensity"], "dauer_typ": st["duration_type"],
            "dauer_wert": st["duration_value"], "ziel_typ": st["target_type"],
            "ziel_pace": (f"{pace(st['target_speed_high'])}-{pace(st['target_speed_low'])}"
                          if st["target_speed_low"] and st["target_speed_high"] else None),
            "ziel_hf": [st["target_hr_low"], st["target_hr_high"]] if st["target_hr_low"] else None,
            "wiederholungen": st["repeat_steps"],
        } for st in steps]
    if lengths:
        out["bahnen"] = [{
            "nr": ln["length_index"] + 1, "typ": ln["length_type"], "stil": ln["swim_stroke"],
            "sekunden": _r(ln["timer_s"]), "zuege": ln["strokes"], "zugfrequenz": ln["cadence"],
        } for ln in lengths]
    if sets:
        out["saetze"] = [{
            "nr": st["set_index"] + 1, "typ": st["set_type"], "sekunden": _r(st["duration_s"], 0),
            "wiederholungen": st["repetitions"], "gewicht": st["weight"],
        } for st in sets]
    return out


TIMESERIES_FIELDS = {
    "hr": "AVG(hr)", "max_hr": "MAX(hr)", "speed": "AVG(speed)", "distance_m": "MAX(distance_m)",
    "altitude_m": "AVG(altitude_m)", "cadence": "AVG(cadence)", "power": "AVG(power)",
    "gct_ms": "AVG(gct_ms)", "gct_balance_pct": "AVG(gct_balance_pct)", "vo_mm": "AVG(vo_mm)",
    "vr_pct": "AVG(vr_pct)", "step_length_mm": "AVG(step_length_mm)", "resp": "AVG(resp)", "temp": "AVG(temp)",
}


def timeseries(activity_id: int, resolution_s: int = 10, fields=None, start_s=None, end_s=None,
               db_file=None) -> dict:
    res = max(1, int(resolution_s or 10))
    wanted = [f for f in (fields or ["hr", "speed", "cadence", "power", "altitude_m"]) if f in TIMESERIES_FIELDS]
    if not wanted:
        raise ValueError(f"Unbekannte Felder. Möglich: {', '.join(TIMESERIES_FIELDS)}")
    where, args = ["activity_id = ?"], [activity_id]
    if start_s is not None:
        where.append("t >= ?"); args.append(int(start_s))
    if end_s is not None:
        where.append("t <= ?"); args.append(int(end_s))
    cols = ", ".join(f"{TIMESERIES_FIELDS[f]} AS {f}" for f in wanted)
    sql = (f"SELECT (t / {res}) * {res} AS bucket, {cols} FROM fit_records "
           f"WHERE {' AND '.join(where)} GROUP BY bucket ORDER BY bucket")
    with connect(db_file) as c:
        rows = c.execute(sql, args).fetchall()
    truncated = len(rows) > MAX_ROWS
    rows = rows[:MAX_ROWS]
    data = []
    for r in rows:
        d = {"t": r["bucket"]}
        for f in wanted:
            v = r[f]
            if f == "speed":
                d["pace"] = pace(v); d["speed"] = _r(v, 2)
            else:
                d[f] = _r(v)
        data.append(d)
    out = {"activity_id": activity_id, "aufloesung_s": res, "punkte": len(data),
           "abgeschnitten": truncated, "werte": data,
           "hinweis": "t = Sekunden seit Start. Bei 'abgeschnitten' gröbere Auflösung oder start_s/end_s nutzen."}
    if not data:
        out["hinweis"] = "Keine Sekundenwerte: FIT-Datei dieser Einheit ist noch nicht ausgewertet."
    return out


# --- Tageswerte ---------------------------------------------------------------

def _first(x):
    return x[0] if isinstance(x, list) and x else x


def _dig(d, *path):
    for p in path:
        d = _first(d)
        if not isinstance(d, dict):
            return None
        d = d.get(p)
    return d


def _training_status(ts):
    """Garmin verschachtelt den Trainingsstatus pro Gerät; wir nehmen das erste Gerät."""
    out = {}
    latest = _dig(ts, "mostRecentTrainingStatus", "latestTrainingStatusData")
    dev = next(iter(latest.values()), None) if isinstance(latest, dict) and latest else None
    if isinstance(dev, dict):
        out["status"] = dev.get("trainingStatusFeedbackPhrase") or dev.get("trainingStatus")
        acute = dev.get("acuteTrainingLoadDTO") or {}
        out["akute_last"] = acute.get("dailyTrainingLoadAcute")
        out["chronische_last"] = acute.get("dailyTrainingLoadChronic")
        out["last_verhaeltnis"] = acute.get("dailyAcuteChronicWorkloadRatio")
    bal = _dig(ts, "mostRecentTrainingLoadBalance", "metricsTrainingLoadBalanceDTOMap")
    bal = next(iter(bal.values()), None) if isinstance(bal, dict) and bal else None
    if isinstance(bal, dict):
        out["last_aerob_niedrig"] = _r(bal.get("monthlyLoadAerobicLow"), 0)
        out["last_aerob_hoch"] = _r(bal.get("monthlyLoadAerobicHigh"), 0)
        out["last_anaerob"] = _r(bal.get("monthlyLoadAnaerobic"), 0)
    vo2 = _dig(ts, "mostRecentVO2Max", "generic", "vo2MaxPreciseValue")
    if vo2:
        out["vo2max"] = vo2
    acc = _dig(ts, "mostRecentVO2Max", "heatAltitudeAcclimation") or _dig(ts, "heatAltitudeAcclimationDTO")
    if isinstance(acc, dict):
        out["hitze_akklimatisierung_pct"] = acc.get("heatAcclimationPercentage")
        out["hitze_trend"] = acc.get("heatTrend")
        if acc.get("altitudeAcclimation"):
            out["hoehen_akklimatisierung"] = acc.get("altitudeAcclimation")
            out["hoehen_trend"] = acc.get("altitudeTrend")
    return out


# --- Trainingsbereitschaft ----------------------------------------------------

READINESS_FACTORS = {
    "schlaf": "sleepScoreFactorPercent",
    "erholungszeit": "recoveryTimeFactorPercent",
    "last_verhaeltnis": "acwrFactorPercent",
    "stressverlauf": "stressHistoryFactorPercent",
    "hrv": "hrvFactorPercent",
    "schlafverlauf": "sleepHistoryFactorPercent",
}

READINESS_CONTEXT = {
    "AFTER_WAKEUP_RESET": "aufwachen",
    "AFTER_POST_EXERCISE_RESET": "nach_training",
    "UPDATE_REALTIME_VARIABLES": "zwischenstand",
}


def _readiness_entries(tr) -> list:
    if isinstance(tr, dict):
        tr = [tr]
    if not isinstance(tr, list):
        return []
    entries = [e for e in tr if isinstance(e, dict) and e.get("score") is not None]
    return sorted(entries, key=lambda e: e.get("timestampLocal") or e.get("timestamp") or "")


def _readiness_split(tr):
    """(morgens, abends): morgens = Wert nach dem Aufwachen, abends = letzter Wert des Tages."""
    entries = _readiness_entries(tr)
    if not entries:
        return None, None
    wake = [e for e in entries if e.get("inputContext") == "AFTER_WAKEUP_RESET"]
    morning = wake[0] if wake else entries[0]
    return morning, entries[-1]


def _readiness_factors(e: dict) -> dict:
    return {k: e.get(v) for k, v in READINESS_FACTORS.items() if e.get(v) is not None}


def _readiness_entry_out(e: dict) -> dict:
    return _clean({
        "uhrzeit": (e.get("timestampLocal") or "")[11:16] or None,
        "anlass": READINESS_CONTEXT.get(e.get("inputContext"), e.get("inputContext")),
        "score": e.get("score"), "stufe": e.get("level"),
        "erholungszeit_h": _h(e.get("recoveryTime")),
        "akute_last": e.get("acuteLoad"),
        "hinweis": e.get("feedbackShort"), "hinweis_lang": e.get("feedbackLong"),
        "faktoren_pct": _readiness_factors(e),
        "hrv_7tage": e.get("hrvWeeklyAverage"), "schlafwert": e.get("sleepScore"),
    })


def readiness_detail(day: str = None, db_file=None) -> dict:
    day = (day or date.today().isoformat())[:10]
    with connect(db_file) as c:
        row = c.execute("SELECT data FROM daily_raw WHERE day=? AND source='training_readiness'", (day,)).fetchone()
        acts = c.execute(
            "SELECT a.activity_id, a.start_local, a.name, a.type_key, a.duration_s FROM activities a "
            "WHERE a.start_local >= ? AND a.start_local < ? ORDER BY a.start_local",
            (day, _next_day(day))).fetchall()
    entries = _readiness_entries(_loads(row["data"]) if row else None)
    if not entries:
        return {"tag": day, "hinweis": "Keine Trainingsbereitschaft für diesen Tag gespeichert."}
    morning, evening = _readiness_split(entries)
    return {
        "tag": day,
        "morgens": morning.get("score"), "abends": evening.get("score"),
        "tagesbilanz": evening.get("score") - morning.get("score"),
        "eintraege": [_readiness_entry_out(e) for e in entries],
        "einheiten": [_clean({
            "activity_id": a["activity_id"], "start": (a["start_local"] or "")[11:16], "name": a["name"],
            "sport_gruppe": sports.group(a["type_key"]), "minuten": _min(a["duration_s"]),
        }) for a in acts],
        "hinweis": "Ein Eintrag 'nach_training' folgt kurz auf das Ende einer Einheit.",
    }


# --- Tageswerte ---------------------------------------------------------------

def _valid_disruptions(sleep):
    vals = [x.get("value") for x in (_dig(sleep, "breathingDisruptionData") or []) if isinstance(x, dict)]
    vals = [v for v in vals if isinstance(v, (int, float)) and 0 <= v < 255]
    return max(vals) if vals else None


def _daily_row(day: str, src: dict) -> dict:
    hrv, sleep, us = src.get("hrv"), src.get("sleep"), src.get("user_summary")
    tr, mm = src.get("training_readiness"), src.get("max_metrics")
    sleep_dto = _dig(sleep, "dailySleepDTO") or {}
    morning, evening = _readiness_split(tr)
    row = {
        "tag": day,
        "hrv_nacht": _dig(hrv, "hrvSummary", "lastNightAvg"),
        "hrv_7tage": _dig(hrv, "hrvSummary", "weeklyAvg"),
        "hrv_status": _dig(hrv, "hrvSummary", "status"),
        "hrv_basis_von": _dig(hrv, "hrvSummary", "baseline", "balancedLow"),
        "hrv_basis_bis": _dig(hrv, "hrvSummary", "baseline", "balancedUpper"),
        "ruhepuls": _dig(us, "restingHeartRate"),
        "ruhepuls_7tage": _dig(us, "lastSevenDaysAvgRestingHeartRate"),
        "stress_avg": _dig(us, "averageStressLevel"),
        "body_battery_max": _dig(us, "bodyBatteryHighestValue"),
        "body_battery_min": _dig(us, "bodyBatteryLowestValue"),
        "body_battery_aufgeladen": _dig(us, "bodyBatteryChargedValue"),
        "body_battery_aufwachen": _dig(us, "bodyBatteryAtWakeTime"),
        "body_battery_nacht_plus": _dig(sleep, "bodyBatteryChange"),
        "schritte": _dig(us, "totalSteps"),
        "schlaf_h": _r((sleep_dto.get("sleepTimeSeconds") or 0) / 3600, 2) or None,
        "tiefschlaf_h": _r((sleep_dto.get("deepSleepSeconds") or 0) / 3600, 2) or None,
        "rem_h": _r((sleep_dto.get("remSleepSeconds") or 0) / 3600, 2) or None,
        "schlaf_wach_min": _min(sleep_dto.get("awakeSleepSeconds")),
        "schlaf_beginn": _local_clock(sleep_dto.get("sleepStartTimestampLocal")),
        "schlaf_ende": _local_clock(sleep_dto.get("sleepEndTimestampLocal")),
        "schlafwert": _dig(sleep_dto, "sleepScores", "overall", "value"),
        "schlaf_unruhe": _dig(sleep, "restlessMomentsCount"),
        "stress_schlaf": sleep_dto.get("avgSleepStress"),
        "atem_schlaf": sleep_dto.get("averageRespirationValue"),
        "atem_wach": _dig(us, "avgWakingRespirationValue"),
        "haut_temp_abweichung": _dig(sleep, "avgSkinTempDeviationC"),
        "spo2_avg": _first_not_none(_dig(us, "averageSpo2"), sleep_dto.get("averageSpO2Value")),
        "spo2_min": _first_not_none(_dig(us, "lowestSpo2"), sleep_dto.get("lowestSpO2Value")),
        "atemaussetzer": _valid_disruptions(sleep),
        "puls_warnungen": _dig(us, "abnormalHeartRateAlertsCount") or None,
        "intensitaetsminuten_moderat": _dig(us, "moderateIntensityMinutes"),
        "intensitaetsminuten_intensiv": _dig(us, "vigorousIntensityMinutes"),
        "stress_hoch_min": _min(_dig(us, "highStressDuration")),
        "stress_mittel_min": _min(_dig(us, "mediumStressDuration")),
        "stress_niedrig_min": _min(_dig(us, "lowStressDuration")),
        "stress_ruhe_min": _min(_dig(us, "restStressDuration")),
        "vo2max": _dig(mm, "generic", "vo2MaxPreciseValue"),
    }
    if morning:
        row.update({
            "bereitschaft": morning.get("score"),
            "bereitschaft_stufe": morning.get("level"),
            "erholungszeit_morgen_h": _h(morning.get("recoveryTime")),
            "bereitschaft_faktoren_morgen": _readiness_factors(morning) or None,
            "bereitschaft_hinweis": morning.get("feedbackShort"),
        })
    if evening and evening is not morning:
        row.update({
            "bereitschaft_abend": evening.get("score"),
            "bereitschaft_abend_stufe": evening.get("level"),
            "erholungszeit_abend_h": _h(evening.get("recoveryTime")),
            "bereitschaft_delta": evening.get("score") - morning.get("score"),
        })
    row["_abend_score"] = evening.get("score") if evening else None
    row.update({k: v for k, v in _training_status(src.get("training_status")).items() if v is not None})
    return row


def daily_metrics(start_date=None, end_date=None, db_file=None) -> list:
    end = end_date or date.today().isoformat()
    start = start_date or (date.fromisoformat(end) - timedelta(days=27)).isoformat()
    before = (date.fromisoformat(start) - timedelta(days=1)).isoformat()  # für Regeneration über Nacht
    with connect(db_file) as c:
        rows = c.execute(
            "SELECT day, source, data FROM daily_raw WHERE day BETWEEN ? AND ? AND data IS NOT NULL ORDER BY day",
            (before, end)).fetchall()
        notes = _notes_between(c, start, end)
    by_day = {}
    for r in rows:
        by_day.setdefault(r["day"], {})[r["source"]] = _loads(r["data"])
    built = [_daily_row(d, src) for d, src in sorted(by_day.items())]
    prev_evening, prev_day = None, None
    out = []
    for row in built:
        day = row["tag"]
        evening = row.pop("_abend_score")
        if prev_evening is not None and row.get("bereitschaft") is not None and prev_day == _prev_day(day):
            row["regeneration_nacht"] = row["bereitschaft"] - prev_evening
        prev_evening, prev_day = evening, day
        if day < start:
            continue
        day_notes = [_note_text(n) for n in notes if n["day_from"] <= day <= n["day_to"]]
        if day_notes:
            row["notizen"] = day_notes
        out.append(_clean(row))
    return out[-400:]


def _prev_day(d: str) -> str:
    return (date.fromisoformat(d) - timedelta(days=1)).isoformat()


# --- Wochenumfänge ------------------------------------------------------------

def weekly_summary(weeks: int = 12, sport=None, db_file=None) -> dict:
    weeks = max(1, min(int(weeks or 12), 260))
    today = date.today()
    start = (today - timedelta(days=today.weekday()) - timedelta(weeks=weeks - 1)).isoformat()
    where, args = ["a.start_local >= ?"], [start]
    if sport:
        where.append("(sport_gruppe(a.type_key, s.sport) = ? OR a.type_key = ? OR s.sport = ?)")
        args += [sport.strip().lower()] * 3
    with connect(db_file) as c:
        rows = c.execute(
            f"SELECT {_LIST_COLS} FROM activities a LEFT JOIN fit_sessions s USING(activity_id) "
            f"WHERE {' AND '.join(where)} ORDER BY a.start_local", args).fetchall()
        notes = _notes_between(c, start, today.isoformat())
    explicit = bool(sport)
    week_map = {}
    for r in rows:
        js = _loads(r["a_data"]) or {}
        m = _merged(r, js)
        if m["group"] in sports.NO_TRAINING and not explicit:
            continue
        wk = _iso_week(r["start_local"])
        w = week_map.setdefault(wk, {"groups": {}})
        g = w["groups"].setdefault(m["group"], {"einheiten": 0, "km": 0.0, "s": 0.0, "last": 0.0,
                                                "hm": 0.0, "hr_s": 0.0, "hr_t": 0.0, "run_m": 0.0, "run_t": 0.0})
        dur = m["timer_s"] or 0
        g["einheiten"] += 1
        g["km"] += (m["distance_m"] or 0) / 1000
        g["s"] += dur
        g["last"] += m["load"] or 0
        g["hm"] += m["ascent"] or 0
        if m["avg_hr"] and dur:
            g["hr_s"] += m["avg_hr"] * dur
            g["hr_t"] += dur
        if m["distance_m"] and dur:
            g["run_m"] += m["distance_m"]
            g["run_t"] += dur
    out = []
    for wk in sorted(week_map):
        y, n = wk.split("-W")
        monday = date.fromisocalendar(int(y), int(n), 1)
        groups, total = [], {"einheiten": 0, "stunden": 0.0, "last": 0.0}
        for name, g in sorted(week_map[wk]["groups"].items(), key=lambda kv: -kv[1]["s"]):
            e = _clean({
                "sport_gruppe": name, "einheiten": g["einheiten"],
                "km": round(g["km"], 1) if g["km"] else None, "stunden": round(g["s"] / 3600, 2),
                "last": round(g["last"]) if g["last"] else None,
                "hoehenmeter": round(g["hm"]) if g["hm"] else None,
                "hf_avg": round(g["hr_s"] / g["hr_t"]) if g["hr_t"] else None,
                "pace_avg": pace(g["run_m"] / g["run_t"]) if name == "laufen" and g["run_t"] else None,
            })
            groups.append(e)
            total["einheiten"] += g["einheiten"]
            total["stunden"] += g["s"] / 3600
            total["last"] += g["last"]
        week_notes = [_note_text(nt) for nt in notes
                      if nt["day_from"] <= (monday + timedelta(days=6)).isoformat()
                      and nt["day_to"] >= monday.isoformat()]
        out.append(_clean({
            "woche": wk, "von": monday.isoformat(), "bis": (monday + timedelta(days=6)).isoformat(),
            "gesamt": {"einheiten": total["einheiten"], "stunden": round(total["stunden"], 2),
                       "last": round(total["last"])},
            "sportarten": groups, "notizen": week_notes,
        }))
    return {"wochen": out,
            "hinweis": "ISO-Wochen (Mo–So). Atemübungen o. Ä. zählen nur mit, wenn ausdrücklich gefiltert. "
                       "Nicht ausgewertete Einheiten mit Werten aus Garmin Connect."}


# --- Leistungsprofil (Schnappschüsse) -----------------------------------------

def _lactate_summary(data) -> dict:
    """Laktatschwelle lesbar machen. Garmin liefert das Tempo im latest-Wert in 0,1 m/s
    (0,408 entspricht 4,08 m/s = 4:05 min/km)."""
    sh = (data or {}).get("speed_and_heart_rate") or {}
    pw = (data or {}).get("power") or {}
    speed = sh.get("speed")
    ms = speed * 10 if speed else None
    return _clean({
        "schwelle_hf": sh.get("heartRate"),
        "schwelle_tempo_ms": _r(ms, 2),
        "schwelle_pace": pace(ms),
        "ermittelt_am": (sh.get("calendarDate") or "")[:10] or None,
        "ftp_watt": pw.get("functionalThresholdPower"),
        "watt_pro_kg": _r(pw.get("powerToWeight"), 2),
        "gewicht_kg": pw.get("weight"),
    })


def _history_compact(source: str, chunks: list) -> dict:
    """Verläufe auf das Wesentliche kürzen: Laktatschwelle nur an Änderungen (mit Pace),
    Wettkampfprognosen ein Wert pro Woche. Rohdaten gibt es mit kompakt=False."""
    if source == "lactate_threshold_history":
        out = {}
        for key, conv in (("heart_rate", lambda v: int(v)), ("speed", lambda v: pace(v * 10)),
                          ("power", lambda v: int(v))):
            last, points = None, []
            for ch in chunks:
                for e in (ch.get(key) or []) if isinstance(ch, dict) else []:
                    v = e.get("value")
                    if v is None:
                        continue
                    cv = conv(v)
                    if cv != last:
                        points.append({"ab": (e.get("from") or "")[:10], "wert": cv})
                        last = cv
            out[{"heart_rate": "schwelle_hf", "speed": "schwelle_pace", "power": "ftp_watt"}[key]] = points
        out["hinweis"] = "Nur Änderungen. Pace aus Garmins 0,1-m/s-Rohwert umgerechnet."
        return out
    if source == "race_predictions_history":
        weekly = {}
        for ch in chunks:
            for e in ch if isinstance(ch, list) else []:
                d = (e.get("calendarDate") or e.get("fromCalendarDate") or "")[:10]
                if not d:
                    continue
                y, w, _ = date.fromisoformat(d).isocalendar()
                weekly[f"{y}-W{w:02d}"] = {k: v for k, v in e.items() if k.startswith("time") or k == "calendarDate"}
        if weekly:
            return {"pro_woche": [{"woche": k, **v} for k, v in sorted(weekly.items())],
                    "hinweis": "Letzter Wert je Woche, Zeiten in s."}
    return {"stuecke": chunks}


def garmin_profile(source: str = None, day: str = None, kompakt: bool = True, db_file=None) -> dict:
    with connect(db_file) as c:
        if not _table_exists(c, "garmin_snapshots"):
            return {"hinweis": "Noch keine Schnappschüsse. Sie entstehen beim nächsten Sync."}
        if not source:
            rows = c.execute(
                "SELECT source, MIN(day) AS von, MAX(day) AS bis, COUNT(*) AS tage, "
                "SUM(data IS NOT NULL) AS mit_daten FROM garmin_snapshots GROUP BY source ORDER BY source").fetchall()
            return {"quellen": [dict(r) for r in rows],
                    "hinweis": "Mit source=<quelle> den Inhalt abrufen (neuester Stand oder day=YYYY-MM-DD). "
                               "Verläufe (*_history) liegen in Jahresstücken und kommen ohne day komplett."}
        if source.endswith("_history") and not day:
            rows = c.execute("SELECT day, fetched_at, data FROM garmin_snapshots WHERE source=? AND data IS NOT NULL "
                             "ORDER BY day", (source,)).fetchall()
            if not rows:
                raise ValueError(f"Kein Verlauf für {source}")
            if kompakt:
                return {"quelle": source, **_history_compact(source, [_loads(r["data"]) for r in rows])}
            return {"quelle": source, "stuecke": [{"ab": r["day"], "abgerufen": r["fetched_at"],
                                                   "daten": _loads(r["data"])} for r in rows]}
        if day:
            row = c.execute("SELECT day, fetched_at, data FROM garmin_snapshots WHERE source=? AND day=?",
                            (source, day[:10])).fetchone()
        else:
            row = c.execute("SELECT day, fetched_at, data FROM garmin_snapshots WHERE source=? AND data IS NOT NULL "
                            "ORDER BY day DESC LIMIT 1", (source,)).fetchone()
    if not row:
        raise ValueError(f"Kein Schnappschuss für {source}")
    out = {"quelle": source, "tag": row["day"], "abgerufen": row["fetched_at"]}
    data = _loads(row["data"])
    if source == "lactate_threshold":
        out["auswertung"] = _lactate_summary(data)
    out["daten"] = data
    return out


# --- Trainingsplan, Wettkämpfe, Berichte ---------------------------------------

RACE_STATUS = ("geplant", "abgesagt", "erledigt")
REPORT_KINDS = ("morgen", "training", "abend")
WEEKDAYS = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _race_out(r) -> dict:
    d = dict(r)
    days = (date.fromisoformat(d["day"]) - date.today()).days
    return _clean({"id": d["id"], "datum": d["day"], "name": d["name"], "sport": d["sport"],
                   "distanz_km": d["distance_km"], "prioritaet": d["priority"], "status": d["status"],
                   "notiz": d["note"], "in_tagen": days, "in_wochen": _r(days / 7, 1)})


def get_plan(db_file=None) -> dict:
    today = date.today()
    with connect(db_file) as c:
        plan = c.execute("SELECT created_at, text FROM coach_plan ORDER BY id DESC LIMIT 1").fetchone() \
            if _table_exists(c, "coach_plan") else None
        races = c.execute("SELECT * FROM races ORDER BY day").fetchall() if _table_exists(c, "races") else []
    upcoming = [_race_out(r) for r in races if r["day"] >= today.isoformat() and r["status"] == "geplant"]
    past_or_off = [_race_out(r) for r in races if not (r["day"] >= today.isoformat() and r["status"] == "geplant")]
    return {
        "heute": today.isoformat(),
        "wochentag": WEEKDAYS[today.weekday()],
        "plan": plan["text"] if plan else None,
        "plan_stand": plan["created_at"] if plan else None,
        "naechste_wettkaempfe": upcoming,
        "weitere_wettkaempfe": past_or_off,
        "hinweis": None if plan else "Noch kein Plan hinterlegt (set_plan).",
    }


def set_plan(text: str, db_file=None) -> dict:
    if not (text or "").strip():
        raise ValueError("Plan darf nicht leer sein")
    with connect_rw(db_file) as c:
        cur = c.execute("INSERT INTO coach_plan(created_at, text) VALUES(?,?)", (_now_utc(), text.strip()[:20000]))
        n = c.execute("SELECT COUNT(*) FROM coach_plan").fetchone()[0]
    return {"id": cur.lastrowid, "version": n, "zeichen": len(text.strip())}


def set_race(day: str, name: str, sport: str = None, distance_km: float = None, priority: str = None,
             status: str = "geplant", note: str = None, race_id: int = None, db_file=None) -> dict:
    d = date.fromisoformat(day[:10]).isoformat()
    status = (status or "geplant").strip().lower()
    if status not in RACE_STATUS:
        raise ValueError(f"Status muss einer von {', '.join(RACE_STATUS)} sein")
    if not (name or "").strip():
        raise ValueError("Name fehlt")
    prio = (priority or "").strip().upper() or None
    vals = (d, name.strip(), sport, distance_km, prio, status, note, _now_utc())
    with connect_rw(db_file) as c:
        if race_id:
            n = c.execute("UPDATE races SET day=?, name=?, sport=?, distance_km=?, priority=?, status=?, note=?, "
                          "updated_at=? WHERE id=?", vals + (int(race_id),)).rowcount
            if not n:
                raise ValueError(f"Wettkampf {race_id} nicht gefunden")
            rid = int(race_id)
        else:
            rid = c.execute("INSERT INTO races(day, name, sport, distance_km, priority, status, note, updated_at) "
                            "VALUES(?,?,?,?,?,?,?,?)", vals).lastrowid
        row = c.execute("SELECT * FROM races WHERE id=?", (rid,)).fetchone()
    return _race_out(row)


def delete_race(race_id: int, db_file=None) -> dict:
    with connect_rw(db_file) as c:
        n = c.execute("DELETE FROM races WHERE id=?", (int(race_id),)).rowcount
    if not n:
        raise ValueError(f"Wettkampf {race_id} nicht gefunden")
    return {"geloescht": int(race_id)}


def pending_reports(days: int = 2, db_file=None) -> dict:
    """Einheiten der letzten Tage ohne Trainingsbericht, dazu ob Morgen-/Abendbericht heute fehlt."""
    today = date.today()
    start = (today - timedelta(days=max(1, int(days)) - 1)).isoformat()
    with connect(db_file) as c:
        acts = c.execute(
            "SELECT a.activity_id, a.start_local, a.name, a.type_key, s.sport AS fit_sport, "
            "s.activity_id IS NOT NULL AS ausgewertet FROM activities a "
            "LEFT JOIN fit_sessions s USING(activity_id) "
            "LEFT JOIN coach_reports r ON r.activity_id = a.activity_id "
            "WHERE a.start_local >= ? AND r.id IS NULL ORDER BY a.start_local", (start,)).fetchall()
        done = {r[0] for r in c.execute("SELECT kind FROM coach_reports WHERE day=? AND activity_id IS NULL",
                                        (today.isoformat(),))}
        last_sync = c.execute("SELECT started_at, finished_at, status, message FROM sync_log "
                              "ORDER BY id DESC LIMIT 1").fetchone()
    offen = []
    for a in acts:
        group = sports.group(a["type_key"], a["fit_sport"])
        if group == "kein_sport":
            continue
        offen.append({"activity_id": a["activity_id"], "start": a["start_local"], "name": a["name"],
                      "sport_gruppe": group, "ausgewertet": bool(a["ausgewertet"])})
    return {
        "heute": today.isoformat(),
        "einheiten_ohne_bericht": offen,
        "morgenbericht_fehlt": "morgen" not in done,
        "abendbericht_fehlt": "abend" not in done,
        "letzter_sync": dict(last_sync) if last_sync else None,
        "hinweis": "Einheiten mit ausgewertet=false haben noch keine FIT-Auswertung; ggf. sync_now abwarten.",
    }


def save_report(kind: str, summary: str, activity_id: int = None, day: str = None, db_file=None) -> dict:
    kind = (kind or "").strip().lower()
    if kind not in REPORT_KINDS:
        raise ValueError(f"Art muss eine von {', '.join(REPORT_KINDS)} sein")
    if kind == "training" and not activity_id:
        raise ValueError("Trainingsbericht braucht activity_id")
    d = date.fromisoformat((day or date.today().isoformat())[:10]).isoformat()
    with connect_rw(db_file) as c:
        if activity_id:
            row = c.execute("SELECT start_local FROM activities WHERE activity_id=?", (int(activity_id),)).fetchone()
            if not row:
                raise ValueError(f"Aktivität {activity_id} nicht gefunden")
            d = (row["start_local"] or d)[:10]
            if c.execute("SELECT 1 FROM coach_reports WHERE activity_id=?", (int(activity_id),)).fetchone():
                return {"hinweis": f"Für Aktivität {activity_id} gibt es schon einen Bericht", "doppelt": True}
        rid = c.execute("INSERT INTO coach_reports(kind, day, activity_id, created_at, summary) VALUES(?,?,?,?,?)",
                        (kind, d, int(activity_id) if activity_id else None, _now_utc(),
                         (summary or "").strip()[:4000])).lastrowid
    return {"id": rid, "art": kind, "tag": d, "activity_id": activity_id}


def list_reports(days: int = 14, kind: str = None, db_file=None) -> list:
    start = (date.today() - timedelta(days=max(1, int(days)) - 1)).isoformat()
    sql = "SELECT id, kind, day, activity_id, created_at, summary FROM coach_reports WHERE day >= ?"
    params = [start]
    if kind:
        sql += " AND kind = ?"
        params.append(kind.strip().lower())
    with connect(db_file) as c:
        if not _table_exists(c, "coach_reports"):
            return []
        rows = c.execute(sql + " ORDER BY day DESC, id DESC", params).fetchall()
    return [_clean({"id": r["id"], "art": r["kind"], "tag": r["day"], "activity_id": r["activity_id"],
                    "erstellt": r["created_at"], "zusammenfassung": r["summary"]}) for r in rows]


# --- Freie Abfrage ------------------------------------------------------------

def schema(db_file=None) -> dict:
    with connect(db_file) as c:
        tables = [r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        out = {t: [f"{col[1]} {col[2]}".strip() for col in c.execute(f"PRAGMA table_info({t})")] for t in tables}
    out["_funktionen"] = ["sport_gruppe(type_key [, fit_sport]) -> laufen | rad | schwimmen | kraft_hiit | "
                          "gehen | kein_sport | sonstiges"]
    return out


def run_sql(sql: str, db_file=None) -> dict:
    stripped = sql.strip().rstrip(";").strip()
    head = stripped.split(None, 1)[0].lower() if stripped else ""
    if head not in ("select", "with"):
        raise ValueError("Nur SELECT- oder WITH-Abfragen sind erlaubt")
    if ";" in stripped:
        raise ValueError("Bitte nur eine Abfrage ohne ';'")
    with connect(db_file) as c:
        cur = c.execute(stripped)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchmany(MAX_ROWS + 1)
    truncated = len(rows) > MAX_ROWS
    return {"spalten": cols, "zeilen": [list(r) for r in rows[:MAX_ROWS]],
            "anzahl": min(len(rows), MAX_ROWS), "abgeschnitten": truncated}
