"""Lese-Abfragen auf garmin.db für den MCP-Server.

Alle Funktionen öffnen die Datenbank nur lesend und geben JSON-taugliche
Dicts in gut lesbaren Einheiten zurück (km, min/km, Minuten, Schritte/min).
"""

import json
import sqlite3
import time
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path

DB_FILE = Path("/share/garmin-coach/garmin.db")

MAX_ROWS = 2000
QUERY_TIMEOUT_S = 15


@contextmanager
def connect(db_file: Path = None):
    path = Path(db_file or DB_FILE)
    if not path.exists():
        raise FileNotFoundError(f"Datenbank {path} existiert noch nicht")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    deadline = time.monotonic() + QUERY_TIMEOUT_S
    conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10000)
    try:
        yield conn
    finally:
        conn.close()


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


def _loads(v):
    if v is None:
        return None
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return v


# --- Überblick ----------------------------------------------------------------

def overview(db_file=None) -> dict:
    with connect(db_file) as c:
        one = lambda sql: c.execute(sql).fetchone()
        daily = one("SELECT MIN(day), MAX(day), COUNT(DISTINCT day) FROM daily_raw WHERE data IS NOT NULL")
        acts = one("SELECT MIN(start_local), MAX(start_local), COUNT(*), SUM(fit_path IS NOT NULL) FROM activities")
        parsed = one("SELECT COUNT(*) FROM fit_sessions")[0]
        errors = one("SELECT COUNT(*) FROM fit_errors")[0]
        sports = [dict(r) for r in c.execute(
            "SELECT sport, COUNT(*) AS anzahl, ROUND(SUM(distance_m)/1000,1) AS km, "
            "ROUND(SUM(timer_s)/3600,1) AS stunden FROM fit_sessions GROUP BY sport ORDER BY anzahl DESC")]
        last_sync = one("SELECT started_at, finished_at, status, message FROM sync_log ORDER BY id DESC LIMIT 1")
    return {
        "tageswerte": {"von": daily[0], "bis": daily[1], "tage": daily[2]},
        "aktivitaeten": {"von": acts[0], "bis": acts[1], "anzahl": acts[2], "mit_fit": acts[3],
                         "ausgewertet": parsed, "fehlerhaft": errors},
        "sportarten": sports,
        "letzter_sync": dict(last_sync) if last_sync else None,
    }


# --- Aktivitäten --------------------------------------------------------------

def list_activities(start_date=None, end_date=None, sport=None, limit=50, db_file=None) -> list:
    where, args = ["1=1"], []
    if start_date:
        where.append("a.start_local >= ?"); args.append(start_date)
    if end_date:
        where.append("a.start_local < ?"); args.append(_next_day(end_date))
    if sport:
        where.append("(s.sport = ? OR a.type_key = ?)"); args += [sport, sport]
    sql = f"""
        SELECT a.activity_id, a.start_local, a.name, a.type_key, s.sport, s.sub_sport,
               COALESCE(s.distance_m, a.distance_m) AS distance_m,
               COALESCE(s.timer_s, a.duration_s) AS timer_s,
               s.avg_speed, COALESCE(s.avg_hr, a.avg_hr) AS avg_hr, COALESCE(s.max_hr, a.max_hr) AS max_hr,
               s.avg_cadence, s.avg_power, s.ascent_m, s.te_aerobic, s.te_anaerobic, s.training_load,
               s.rpe, s.feel, s.efficiency, s.decoupling_pct, s.speed_cv_pct,
               s.target_hit_pct, s.hr_recovery_60s
        FROM activities a LEFT JOIN fit_sessions s ON s.activity_id = a.activity_id
        WHERE {' AND '.join(where)}
        ORDER BY a.start_local DESC LIMIT ?"""
    args.append(min(int(limit or 50), 500))
    with connect(db_file) as c:
        rows = c.execute(sql, args).fetchall()
    return [{
        "activity_id": r["activity_id"],
        "start": r["start_local"],
        "name": r["name"],
        "sport": r["sport"] or r["type_key"],
        "sub_sport": r["sub_sport"],
        "km": _km(r["distance_m"]),
        "minuten": _min(r["timer_s"]),
        "pace": pace(r["avg_speed"]) if (r["sport"] or r["type_key"] or "").startswith(("run", "trail", "walk", "hik")) else None,
        "hf_avg": r["avg_hr"], "hf_max": r["max_hr"],
        "kadenz": r["avg_cadence"], "watt": r["avg_power"], "hoehenmeter": r["ascent_m"],
        "te_aerob": r["te_aerobic"], "te_anaerob": r["te_anaerobic"], "last": _r(r["training_load"], 0),
        "rpe": r["rpe"], "gefuehl": r["feel"],
        "effizienz": r["efficiency"], "drift_pct": r["decoupling_pct"], "tempo_cv_pct": r["speed_cv_pct"],
        "zieltempo_treffer_pct": r["target_hit_pct"], "pulserholung_60s": r["hr_recovery_60s"],
    } for r in rows]


def get_activity(activity_id: int, db_file=None) -> dict:
    with connect(db_file) as c:
        a = c.execute("SELECT * FROM activities WHERE activity_id=?", (activity_id,)).fetchone()
        s = c.execute("SELECT * FROM fit_sessions WHERE activity_id=?", (activity_id,)).fetchone()
        laps = c.execute("SELECT * FROM fit_laps WHERE activity_id=? ORDER BY lap_index", (activity_id,)).fetchall()
        steps = c.execute("SELECT * FROM fit_workout_steps WHERE activity_id=? ORDER BY step_index", (activity_id,)).fetchall()
        lengths = c.execute("SELECT * FROM fit_lengths WHERE activity_id=? ORDER BY length_index", (activity_id,)).fetchall()
        sets = c.execute("SELECT * FROM fit_sets WHERE activity_id=? ORDER BY set_index", (activity_id,)).fetchall()
        err = c.execute("SELECT error FROM fit_errors WHERE activity_id=?", (activity_id,)).fetchone()
    if not a and not s:
        raise ValueError(f"Aktivität {activity_id} nicht gefunden")
    out = {"activity_id": activity_id}
    if a:
        out.update({"name": a["name"], "start_lokal": a["start_local"], "garmin_typ": a["type_key"]})
    if err:
        out["auswertungsfehler"] = err[0]
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
            "zeit_in_hf_zonen_min": [_min(x) for x in (_loads(s["time_in_hr_zone"]) or [])],
            "geraete": _loads(s["devices"]),
            "effizienz": s["efficiency"], "drift_pct": s["decoupling_pct"], "tempo_cv_pct": s["speed_cv_pct"],
            "zieltempo_treffer_pct": s["target_hit_pct"], "zieltempo_runden": s["target_laps"],
            "pulserholung_60s": s["hr_recovery_60s"], "sekundenwerte_puls": s["hr_samples"],
        }
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
    return {"activity_id": activity_id, "aufloesung_s": res, "punkte": len(data),
            "abgeschnitten": truncated, "werte": data,
            "hinweis": "t = Sekunden seit Start. Bei 'abgeschnitten' gröbere Auflösung oder start_s/end_s nutzen."}


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
    return out


def _daily_row(day: str, src: dict) -> dict:
    hrv, sleep, us = src.get("hrv"), src.get("sleep"), src.get("user_summary")
    tr, mm = src.get("training_readiness"), src.get("max_metrics")
    sleep_dto = _dig(sleep, "dailySleepDTO") or {}
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
        "schritte": _dig(us, "totalSteps"),
        "schlaf_h": _r((sleep_dto.get("sleepTimeSeconds") or 0) / 3600, 2) or None,
        "tiefschlaf_h": _r((sleep_dto.get("deepSleepSeconds") or 0) / 3600, 2) or None,
        "rem_h": _r((sleep_dto.get("remSleepSeconds") or 0) / 3600, 2) or None,
        "schlafwert": _dig(sleep_dto, "sleepScores", "overall", "value"),
        "atem_schlaf": sleep_dto.get("averageRespirationValue"),
        "bereitschaft": _dig(tr, "score"),
        "bereitschaft_stufe": _dig(tr, "level"),
        "vo2max": _dig(mm, "generic", "vo2MaxPreciseValue"),
    }
    row.update({k: v for k, v in _training_status(src.get("training_status")).items() if v is not None})
    return row


def _next_day(d: str) -> str:
    return (date.fromisoformat(d[:10]) + timedelta(days=1)).isoformat()


def daily_metrics(start_date=None, end_date=None, db_file=None) -> list:
    end = end_date or date.today().isoformat()
    start = start_date or (date.fromisoformat(end) - timedelta(days=27)).isoformat()
    with connect(db_file) as c:
        rows = c.execute(
            "SELECT day, source, data FROM daily_raw WHERE day BETWEEN ? AND ? AND data IS NOT NULL ORDER BY day",
            (start, end)).fetchall()
    by_day = {}
    for r in rows:
        by_day.setdefault(r["day"], {})[r["source"]] = _loads(r["data"])
    out = [_daily_row(d, src) for d, src in sorted(by_day.items())]
    return [{k: v for k, v in row.items() if v is not None} for row in out[-400:]]


# --- Wochenumfänge ------------------------------------------------------------

def weekly_summary(weeks: int = 12, sport=None, db_file=None) -> list:
    start = (date.today() - timedelta(weeks=int(weeks or 12))).isoformat()
    where, args = ["start_time >= ?"], [start]
    if sport:
        where.append("sport = ?"); args.append(sport)
    sql = f"""
        SELECT strftime('%Y-W%W', start_time) AS woche, sport, COUNT(*) AS einheiten,
               ROUND(SUM(distance_m)/1000, 1) AS km, ROUND(SUM(timer_s)/3600, 2) AS stunden,
               ROUND(SUM(training_load)) AS last, ROUND(SUM(ascent_m)) AS hoehenmeter,
               ROUND(SUM(distance_m)/SUM(timer_s), 3) AS v_avg, ROUND(AVG(avg_hr)) AS hf_avg
        FROM fit_sessions WHERE {' AND '.join(where)}
        GROUP BY woche, sport ORDER BY woche, sport"""
    with connect(db_file) as c:
        rows = c.execute(sql, args).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        v = d.pop("v_avg")
        if d["sport"] == "running":
            d["pace_avg"] = pace(v)
        out.append(d)
    return out


# --- Freie Abfrage ------------------------------------------------------------

def schema(db_file=None) -> dict:
    with connect(db_file) as c:
        tables = [r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {t: [f"{col[1]} {col[2]}".strip() for col in c.execute(f"PRAGMA table_info({t})")] for t in tables}


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
