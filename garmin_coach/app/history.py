"""Wochenhistorie: eine Zeile je Kalenderwoche (ISO, Montag bis Sonntag) seit START_YEAR.

Grundlage für das Dashboard "Garmin History" und das Werkzeug jahres_historie. Die Tabelle
history_weeks wird nach jedem vollen Sync komplett neu berechnet (dauert wenige Sekunden);
Wochen ohne Daten fehlen einfach, im Dashboard bleiben sie leer.

Werte je Woche (Mittel über die Tage bzw. Nächte mit Daten, Summen für Umfänge):
  Recovery  hrv, hrv_lo, hrv_hi (Garmin-Basisbereich), ruhepuls, gewicht (Tagesminimum),
            bereitschaft (nach dem Aufwachen), bb_max (Body Battery Höchstwert), vo2max (letzter Wert)
  Schlaf    schlaf_min, bedarf_min, schlafwert, tief_min, rem_min, leicht_min, wach_min, naechte
  Laufen    lauf_km, lauf_h, laeufe, pace_s (s/km), laengster_km, hm_lauf (Laufen + Gehen),
            hm_treppe, form138_s (Pace bei HF 138 auf flachen Kilometern, s/km)
  Zonen     Minuten je HF-Zone 1–5 für G (gesamt), L (Laufen), S (Schwimmen), H (Kraft/HIIT), A (andere)
"""

import json
import logging
from datetime import date, timedelta

import queries
import sports
from queries import _clean, _dig, _loads, _readiness_split, connect, connect_rw

log = logging.getLogger("garmin_coach.history")

START_YEAR = 2022

# Reihenfolge der Werte in den HA-Sensoren (Attribut 'wochen': [kw, *SPALTEN])
SPALTEN = ("hrv", "hrv_lo", "hrv_hi", "ruhepuls", "gewicht", "bereitschaft", "bb_max", "vo2max",
           "schlaf_min", "bedarf_min", "schlafwert", "tief_min", "rem_min", "leicht_min", "wach_min",
           "lauf_km", "lauf_h", "laeufe", "pace_s", "laengster_km", "hm_lauf", "hm_treppe", "form138_s")
ZONEN = ("G", "L", "S", "H", "A")
RUN_GROUP = {"laufen": "L", "schwimmen": "S", "kraft_hiit": "H"}

TABLE = """
CREATE TABLE IF NOT EXISTS history_weeks (
    woche      TEXT PRIMARY KEY,   -- Montag der ISO-Woche, YYYY-MM-DD
    jahr       INTEGER NOT NULL,   -- ISO-Jahr
    kw         INTEGER NOT NULL,   -- ISO-Kalenderwoche
    data       TEXT NOT NULL,      -- JSON mit den Werten der Woche
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_history_weeks_jahr ON history_weeks(jahr, kw);
"""

# Flache Kilometer-Runden lockerer Läufe (HF 133–143), je Lauf auf HF 138 umgerechnet
FORM_SQL = """
SELECT a.start_local,
       SUM(l.timer_s) / (SUM(l.distance_m) / 1000.0)
         * (SUM(l.avg_hr * l.timer_s) / SUM(l.timer_s)) / 138.0 AS p138
FROM fit_laps l JOIN activities a ON a.activity_id = l.activity_id
JOIN fit_sessions s ON s.activity_id = l.activity_id
WHERE a.type_key = 'running' AND a.start_local >= ?
  AND l.distance_m BETWEEN 900 AND 1100 AND l.avg_hr BETWEEN 133 AND 143
  AND COALESCE(l.ascent_m, 0) <= 6 AND COALESCE(l.descent_m, 0) <= 6 AND COALESCE(s.target_laps, 0) = 0
GROUP BY a.activity_id HAVING SUM(l.distance_m) >= 3000
"""


def _monday(day: str) -> date:
    d = date.fromisoformat(day[:10])
    return d - timedelta(days=d.weekday())


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _avg(vals, n=1):
    v = [x for x in vals if x is not None]
    return round(sum(v) / len(v), n) if v else None


def _zones_of(fit_zones, js: dict):
    """Sekunden in Zone 1–5: aus der FIT-Datei (Index 1–5), sonst aus Garmin Connect."""
    z = _loads(fit_zones) if isinstance(fit_zones, str) else fit_zones
    if isinstance(z, list) and len(z) >= 6:
        return [float(_num(x) or 0) for x in z[1:6]]
    vals = [_num(js.get(f"hrTimeInZone_{i}")) for i in range(1, 6)]
    return [float(v or 0) for v in vals] if any(vals) else None


def _start_day() -> str:
    # erste ISO-Woche des Startjahres kann im Dezember davor beginnen
    return date.fromisocalendar(START_YEAR, 1, 1).isoformat()


def _daily(c, weeks: dict) -> None:
    rows = c.execute("SELECT day, source, data FROM daily_raw WHERE day >= ? AND data IS NOT NULL AND source IN "
                     "('hrv','user_summary','sleep','training_readiness','weight','training_status')",
                     (_start_day(),)).fetchall()
    days = {}
    for r in rows:
        days.setdefault(r["day"], {})[r["source"]] = _loads(r["data"])
    for day, src in days.items():
        w = weeks.setdefault(_monday(day).isoformat(), {"d": [], "a": [], "form": []})
        hrv = src.get("hrv") or {}
        us = src.get("user_summary") or {}
        dto = _dig(src.get("sleep"), "dailySleepDTO") or {}
        sleep_s = _num(dto.get("sleepTimeSeconds"))
        morning, _ = _readiness_split(src.get("training_readiness"))
        wt = src.get("weight") or {}
        kg = _num(wt.get("minWeight")) or _num(_dig(wt, "latestWeight", "weight"))
        w["d"].append({
            "hrv": _num(_dig(hrv, "hrvSummary", "lastNightAvg")),
            "hrv_lo": _num(_dig(hrv, "hrvSummary", "baseline", "balancedLow")),
            "hrv_hi": _num(_dig(hrv, "hrvSummary", "baseline", "balancedUpper")),
            "ruhepuls": _num(us.get("restingHeartRate")),
            "bb_max": _num(us.get("bodyBatteryHighestValue")),
            "bereitschaft": _num((morning or {}).get("score")),
            "gewicht": kg / 1000 if kg and kg > 0 else None,
            "vo2max": _num(_dig(src.get("training_status"), "mostRecentVO2Max", "generic", "vo2MaxPreciseValue")),
            "schlaf": sleep_s / 60 if sleep_s and sleep_s > 0 else None,
            "bedarf": _num(_dig(dto, "sleepNeed", "actual")) if sleep_s else None,
            "schlafwert": _num(_dig(dto, "sleepScores", "overall", "value")),
            "tief": (_num(dto.get("deepSleepSeconds")) or 0) / 60 if sleep_s else None,
            "rem": (_num(dto.get("remSleepSeconds")) or 0) / 60 if sleep_s else None,
            "leicht": (_num(dto.get("lightSleepSeconds")) or 0) / 60 if sleep_s else None,
            "wach": (_num(dto.get("awakeSleepSeconds")) or 0) / 60 if sleep_s else None,
            "tag": day,
        })


def _activities(c, weeks: dict) -> None:
    rows = c.execute(
        "SELECT a.start_local, a.type_key, a.distance_m, a.duration_s, a.data, s.sport AS fit_sport, "
        "s.distance_m AS fit_dist, s.timer_s, s.ascent_m, s.time_in_hr_zone "
        "FROM activities a LEFT JOIN fit_sessions s ON s.activity_id = a.activity_id "
        "WHERE a.start_local >= ?", (_start_day(),)).fetchall()
    for r in rows:
        js = _loads(r["data"]) or {}
        g = sports.group(r["type_key"], r["fit_sport"])
        if g in sports.NO_TRAINING:
            continue
        dist = _num(r["fit_dist"]) if r["fit_dist"] is not None else _num(r["distance_m"])
        secs = _num(r["timer_s"]) or _num(r["duration_s"]) or 0
        ascent = _num(r["ascent_m"])
        if ascent is None:
            ascent = _num(js.get("elevationGain"))
        if g == "treppe":
            ascent = sports.stair_ascent(js, ascent)
        w = weeks.setdefault(_monday(r["start_local"]).isoformat(), {"d": [], "a": [], "form": []})
        w["a"].append({"g": g, "dist": dist or 0, "secs": secs, "ascent": ascent or 0,
                       "zones": _zones_of(r["time_in_hr_zone"], js)})
    for r in c.execute(FORM_SQL, (_start_day(),)).fetchall():
        if r["p138"]:
            weeks.setdefault(_monday(r["start_local"]).isoformat(), {"d": [], "a": [], "form": []})[
                "form"].append(r["p138"])


def _week_values(w: dict) -> dict:
    d, acts = w["d"], w["a"]
    nights = [x for x in d if x["schlaf"]]
    runs = [a for a in acts if a["g"] == "laufen"]
    run_m = sum(a["dist"] for a in runs)
    run_s_dist = sum(a["secs"] for a in runs if a["dist"] > 0)
    vo2 = [x["vo2max"] for x in sorted(d, key=lambda x: x["tag"]) if x["vo2max"]]
    zones = {}
    for a in acts:
        if not a["zones"]:
            continue
        for key in ("G", RUN_GROUP.get(a["g"], "A")):
            z = zones.setdefault(key, [0.0] * 5)
            for i in range(5):
                z[i] += a["zones"][i] / 60
    out = {
        "hrv": _avg([x["hrv"] for x in d]), "hrv_lo": _avg([x["hrv_lo"] for x in d]),
        "hrv_hi": _avg([x["hrv_hi"] for x in d]), "ruhepuls": _avg([x["ruhepuls"] for x in d]),
        "gewicht": _avg([x["gewicht"] for x in d], 2), "bereitschaft": _avg([x["bereitschaft"] for x in d], 0),
        "bb_max": _avg([x["bb_max"] for x in d], 0), "vo2max": vo2[-1] if vo2 else None,
        "schlaf_min": _avg([x["schlaf"] for x in nights], 0),
        "bedarf_min": _avg([x["bedarf"] for x in nights], 0),
        "schlafwert": _avg([x["schlafwert"] for x in d], 0),
        "tief_min": _avg([x["tief"] for x in nights], 0), "rem_min": _avg([x["rem"] for x in nights], 0),
        "leicht_min": _avg([x["leicht"] for x in nights], 0), "wach_min": _avg([x["wach"] for x in nights], 0),
        "naechte": len(nights) or None, "tage": len(d) or None,
        "lauf_km": round(run_m / 1000, 1) if runs else None,
        "lauf_h": round(sum(a["secs"] for a in runs) / 3600, 2) if runs else None,
        "laeufe": len(runs) or None,
        "pace_s": round(run_s_dist / (run_m / 1000)) if run_m > 0 else None,
        "laengster_km": round(max(a["dist"] for a in runs) / 1000, 1) if runs else None,
        "hm_lauf": round(sum(a["ascent"] for a in acts if a["g"] in ("laufen", "gehen"))) if runs else None,
        "hm_treppe": round(sum(a["ascent"] for a in acts if a["g"] == "treppe")) or None,
        "form138_s": round(sum(w["form"]) / len(w["form"])) if w["form"] else None,
        "einheiten": len(acts) or None,
        "stunden": round(sum(a["secs"] for a in acts) / 3600, 2) if acts else None,
        "zonen": {k: [round(x, 1) for x in v] for k, v in zones.items() if sum(v) > 0} or None,
    }
    return {k: v for k, v in out.items() if v is not None}


def compute(db_file=None) -> dict:
    """{montag: werte} für alle Wochen ab START_YEAR mit Daten."""
    weeks = {}
    with connect(db_file) as c:
        _daily(c, weeks)
        _activities(c, weeks)
    out = {}
    for monday, w in weeks.items():
        y, kw, _ = date.fromisoformat(monday).isocalendar()
        if y < START_YEAR:
            continue
        vals = _week_values(w)
        if vals:
            out[monday] = {"jahr": y, "kw": kw, **vals}
    return out


def rebuild(db_file=None) -> int:
    weeks = compute(db_file)
    now = queries.datetime.now(queries.timezone.utc).isoformat(timespec="seconds")
    with connect_rw(db_file) as c:
        c.executescript(TABLE)
        c.execute("DELETE FROM history_weeks")
        c.executemany("INSERT INTO history_weeks(woche, jahr, kw, data, updated_at) VALUES(?,?,?,?,?)",
                      [(m, v["jahr"], v["kw"], json.dumps({k: x for k, x in v.items() if k not in ("jahr", "kw")},
                                                          ensure_ascii=False), now)
                       for m, v in sorted(weeks.items())])
    return len(weeks)


def load(c, jahr: int | None = None) -> list:
    """Wochen aus der Tabelle (leer, wenn sie noch nicht berechnet wurde)."""
    if not queries._table_exists(c, "history_weeks"):
        return []
    sql = "SELECT woche, jahr, kw, data FROM history_weeks"
    rows = c.execute(sql + (" WHERE jahr=?" if jahr else "") + " ORDER BY woche", (jahr,) if jahr else ()).fetchall()
    return [{"woche": r["woche"], "jahr": r["jahr"], "kw": r["kw"], **(_loads(r["data"]) or {})} for r in rows]


# --- Zusammenfassung für den Coach ------------------------------------------------

def _sum(ws, k, n=1):
    v = [w[k] for w in ws if w.get(k) is not None]
    return round(sum(v), n) if v else None


def _wavg(ws, k, n=1):
    return _avg([w.get(k) for w in ws], n)


def summary(ws: list) -> dict:
    km = _sum(ws, "lauf_km")
    secs = sum((w.get("pace_s") or 0) * (w.get("lauf_km") or 0) for w in ws if w.get("pace_s"))
    km_p = sum(w.get("lauf_km") or 0 for w in ws if w.get("pace_s"))
    zg = [0.0] * 5
    for w in ws:
        for i, x in enumerate(((w.get("zonen") or {}).get("G")) or []):
            zg[i] += x
    tz = sum(zg)
    vo2 = [w["vo2max"] for w in ws if w.get("vo2max")]
    return _clean({
        "wochen": len(ws) or None,
        "lauf_km": km, "laeufe": _sum(ws, "laeufe", 0), "lauf_h": _sum(ws, "lauf_h"),
        "lauf_km_pro_woche": round(km / len(ws), 1) if km and ws else None,
        "pace": queries.pace(1000 / (secs / km_p)) if km_p and secs else None,
        "laengster_km": max([w.get("laengster_km") or 0 for w in ws], default=None) or None,
        "hm_lauf": _sum(ws, "hm_lauf", 0), "hm_treppe": _sum(ws, "hm_treppe", 0),
        "stunden_gesamt": _sum(ws, "stunden"),
        "hrv_avg": _wavg(ws, "hrv"), "ruhepuls_avg": _wavg(ws, "ruhepuls"),
        "schlaf_h_avg": round(_wavg(ws, "schlaf_min") / 60, 2) if _wavg(ws, "schlaf_min") else None,
        "schlafwert_avg": _wavg(ws, "schlafwert", 0), "gewicht_avg": _wavg(ws, "gewicht", 2),
        "vo2max_anfang": vo2[0] if vo2 else None, "vo2max_ende": vo2[-1] if vo2 else None,
        "form138_pace": queries.pace(1000 / _wavg(ws, "form138_s", 0)) if _wavg(ws, "form138_s", 0) else None,
        "zonen_anteil_pct": [round(x / tz * 100) for x in zg] if tz else None,
    })


def year_report(jahr: int | None = None, bis_kw: int | None = None, db_file=None) -> dict:
    """Wochenwerte eines Jahres plus Bilanz und Vorjahr bis zur gleichen Kalenderwoche."""
    today = date.today()
    jahr = int(jahr or today.isocalendar()[0])
    with connect(db_file) as c:
        ws = load(c, jahr)
        prev = load(c, jahr - 1)
    if not ws and not prev:
        return {"jahr": jahr, "hinweis": "Keine Wochenhistorie für dieses Jahr (Daten ab 2022; Neuberechnung "
                                         "nach dem nächsten vollen Sync)."}
    last_kw = bis_kw or (max(w["kw"] for w in ws) if ws else 53)
    cut = [w for w in ws if w["kw"] <= last_kw]
    prev_cut = [w for w in prev if w["kw"] <= last_kw]
    return {
        "jahr": jahr, "bis_kw": last_kw,
        "bilanz": summary(cut), "vorjahr_bis_gleiche_kw": summary(prev_cut) if prev_cut else None,
        "vorjahr_gesamt": summary(prev) if prev else None,
        "wochen": [{k: v for k, v in w.items() if k != "jahr"} for w in ws],
        "hinweise": [
            "Wochen = ISO-Kalenderwochen (Mo–So); Werte sind Mittel über Tage/Nächte mit Daten, Umfänge Summen.",
            "pace_s und form138_s in Sekunden pro km; Schlafwerte in Minuten pro Nacht; zonen in Minuten je Zone 1–5.",
            "2022: HRV erst ab November, Gewicht selten, Höhenmeter (Forerunner 245) etwa 3× zu hoch.",
            "Puls bis 06/2024 vom Handgelenk, danach Brustgurt: Zonen davor weniger genau. Treppe ab 2025.",
        ],
    }
