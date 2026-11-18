"""Wetter je Aktivität: Garmin-Wetterstation plus Open-Meteo-Stundenwerte.

Garmin liefert pro Aktivität einen Wetterwert der nächsten Station zum Start (Temperatur, gefühlte
Temperatur, Luftfeuchte, Wind, Böen, Wetterlage als Text), aber keine Niederschlagsmenge.
Open-Meteo liefert für die Startkoordinaten stündliche Werte (auch rückwirkend bis 2022) mit
Niederschlag und Schnee; daraus werden Mittel, Spannen und Summen über die Dauer der Einheit.

Die Temperatur der FIT-Datei kommt vom Sensor an der Uhr (körperwarm) und taugt nicht als
Außentemperatur.
"""

import json
import logging
import time
from datetime import date, datetime, timedelta

import requests

from db import Store, now_iso

log = logging.getLogger("garmin_coach.weather")

PAUSE_S = 0.6
OM_RECENT = "https://api.open-meteo.com/v1/forecast"                    # bis ~90 Tage zurück
OM_HISTORY = "https://historical-forecast-api.open-meteo.com/v1/forecast"  # ab 2022
OM_RECENT_DAYS = 60
OM_HOURLY = ("temperature_2m", "apparent_temperature", "relative_humidity_2m", "precipitation",
             "snowfall", "wind_speed_10m", "wind_gusts_10m", "weather_code")
REFRESH_DAYS = 2  # junge Einheiten neu holen: Open-Meteo ergänzt die letzten Stunden nachträglich
RETRY_ERROR_DAYS = 7

# WMO-Wettercodes von Open-Meteo, grob auf Deutsch
WMO = [(95, "Gewitter"), (85, "Schneeschauer"), (80, "Regenschauer"), (71, "Schnee"),
       (66, "gefrierender Regen"), (61, "Regen"), (56, "gefrierender Niesel"), (51, "Nieselregen"),
       (45, "Nebel"), (3, "bedeckt"), (2, "teils bewölkt"), (1, "überwiegend klar"), (0, "klar")]

# Geräte, die nie draußen sind, auch wenn Garmin Koordinaten mitschickt
INDOOR_TYPES = {"treadmill_running", "indoor_cycling", "virtual_ride", "indoor_cardio", "indoor_rowing",
                "lap_swimming", "floor_climbing", "stair_climbing", "strength_training", "hiit", "yoga",
                "pilates", "breathwork", "cross_country_indoor_skiing", "elliptical", "indoor_walking"}


# Garmin liefert die Wetterlage englisch; die häufigsten auf Deutsch, Rest bleibt wie geliefert
GARMIN_DESC = {
    "clear": "klar", "sunny": "sonnig", "mostly sunny": "überwiegend sonnig", "mostly clear": "überwiegend klar",
    "partly cloudy": "teils bewölkt", "mostly cloudy": "überwiegend bewölkt", "cloudy": "bewölkt",
    "overcast": "bedeckt", "fog": "Nebel", "haze": "Dunst", "mist": "Dunst", "drizzle": "Nieselregen",
    "light rain": "leichter Regen", "rain": "Regen", "heavy rain": "starker Regen", "showers": "Regenschauer",
    "rain showers": "Regenschauer", "thunderstorm": "Gewitter", "thunderstorms": "Gewitter",
    "light snow": "leichter Schnee", "snow": "Schnee", "heavy snow": "starker Schnee", "sleet": "Schneeregen",
    "freezing rain": "gefrierender Regen", "freezing drizzle": "gefrierender Niesel", "windy": "windig",
}


def _garmin_desc(desc):
    if not desc:
        return None
    return GARMIN_DESC.get(desc.strip().lower(), desc)


def _wmo_text(code):
    if code is None:
        return None
    for lo, text in WMO:
        if code >= lo:
            return text
    return None


def _r(v, n=1):
    return None if v is None else round(v, n)


def _mean(vals):
    v = [x for x in vals if isinstance(x, (int, float))]
    return sum(v) / len(v) if v else None


def _pick(raw, converted, ref, tol):
    """Garmin meldet je nach Konto °F/mph oder °C/km/h. Mit Open-Meteo als Referenz die passende
    Lesart wählen; ohne Referenz gilt die Umrechnung (Garmin-Standard)."""
    if raw is None:
        return None
    if ref is not None and abs(raw - ref) + tol < abs(converted - ref):
        return raw
    return converted


def parse_garmin(js: dict, ref_temp=None, ref_wind=None) -> dict:
    if not isinstance(js, dict) or not js:
        return {}
    f2c = lambda f: None if f is None else (f - 32) * 5 / 9
    mph = lambda v: None if v is None else v * 1.609344
    temp = _pick(js.get("temp"), f2c(js.get("temp")), ref_temp, 2)
    app = js.get("apparentTemp")
    # gefühlte Temperatur in derselben Lesart wie die Temperatur
    app = None if app is None else (app if temp == js.get("temp") else f2c(app))
    wind = _pick(js.get("windSpeed"), mph(js.get("windSpeed")), ref_wind, 3)
    gust = js.get("windGust")
    gust = None if gust is None else (gust if wind == js.get("windSpeed") else mph(gust))
    return {
        "temp_c": temp, "gefuehlt_c": app, "feuchte_pct": js.get("relativeHumidity"),
        "wind_kmh": wind, "boeen_kmh": gust,
        "wetter": _garmin_desc((js.get("weatherTypeDTO") or {}).get("desc")),
        "station": ((js.get("weatherStationDTO") or {}).get("name")),
        "wind_richtung": js.get("windDirectionCompassPoint"),
    }


def openmeteo_window(hourly: dict, start: datetime, end: datetime) -> dict:
    """Stunden, deren Zeitstempel in [Start abgerundet, Ende aufgerundet] liegt."""
    times = hourly.get("time") or []
    lo = start.replace(minute=0, second=0, microsecond=0)
    hi = end.replace(minute=0, second=0, microsecond=0) + (timedelta(hours=1) if end.minute or end.second else timedelta())
    idx = [i for i, t in enumerate(times) if lo <= datetime.fromisoformat(t) <= hi]
    return {k: [v[i] for i in idx] for k, v in hourly.items() if isinstance(v, list)}


def summarize_openmeteo(win: dict) -> dict:
    if not win.get("time"):
        return {}
    temps = [t for t in win.get("temperature_2m") or [] if t is not None]
    codes = [c for c in win.get("weather_code") or [] if c is not None]
    precip = [p for p in win.get("precipitation") or [] if p is not None]
    snow = [s for s in win.get("snowfall") or [] if s is not None]
    gusts = [g for g in win.get("wind_gusts_10m") or [] if g is not None]
    return {
        "temp_c": _mean(temps), "temp_min_c": min(temps) if temps else None,
        "temp_max_c": max(temps) if temps else None,
        "gefuehlt_c": _mean(win.get("apparent_temperature") or []),
        "feuchte_pct": _mean(win.get("relative_humidity_2m") or []),
        "wind_kmh": _mean(win.get("wind_speed_10m") or []),
        "boeen_kmh": max(gusts) if gusts else None,
        # Stundenwert = Summe der vorangegangenen Stunde; die erste Stunde liegt meist vor dem Start
        "niederschlag_mm": sum(precip[1:] if len(precip) > 1 else precip) if precip else None,
        "schnee_cm": sum(snow[1:] if len(snow) > 1 else snow) if snow else None,
        "wetter": _wmo_text(max(codes)) if codes else None,
    }


def extremes(w: dict) -> list:
    out = []
    tmin, tmax = w.get("temp_min_c", w.get("temp_c")), w.get("temp_max_c", w.get("temp_c"))
    if tmin is not None:
        if tmin <= -5:
            out.append("starker Frost")
        elif tmin <= 0:
            out.append("Frost")
    if tmax is not None:
        if tmax >= 30:
            out.append("Hitze")
        elif tmax >= 25:
            out.append("warm")
    if (w.get("feuchte_pct") or 0) >= 80 and (w.get("temp_c") or 0) >= 20:
        out.append("schwül")
    rain = w.get("niederschlag_mm") or 0
    if rain >= 5:
        out.append("Starkregen")
    elif rain >= 1:
        out.append("Regen")
    if (w.get("schnee_cm") or 0) > 0:
        out.append("Schnee")
    if (w.get("boeen_kmh") or 0) >= 60:
        out.append("Sturmböen")
    elif (w.get("wind_kmh") or 0) >= 30 or (w.get("boeen_kmh") or 0) >= 45:
        out.append("starker Wind")
    if (w.get("wetter") or "").startswith("Gewitter"):
        out.append("Gewitter")
    if "gefrierend" in (w.get("wetter") or ""):
        out.append("Glätte möglich")
    return out


def combine(garmin_js: dict | None, om: dict) -> dict:
    """Open-Meteo zuerst (ganze Dauer, mit Niederschlag), Garmin-Station als Ersatz und Ergänzung."""
    g = parse_garmin(garmin_js or {}, om.get("temp_c"), om.get("wind_kmh"))
    w = {k: om.get(k) for k in ("temp_c", "temp_min_c", "temp_max_c", "gefuehlt_c", "feuchte_pct",
                                 "wind_kmh", "boeen_kmh", "niederschlag_mm", "schnee_cm", "wetter")}
    for k in ("temp_c", "gefuehlt_c", "feuchte_pct", "wind_kmh", "boeen_kmh"):
        if w.get(k) is None:
            w[k] = g.get(k)
    if g.get("wetter") and not w.get("wetter"):
        w["wetter"] = g["wetter"]
    w["station"] = g.get("station")
    w = {k: (_r(v) if isinstance(v, float) else v) for k, v in w.items()}
    w["extrem"] = extremes(w)
    return w


def _activity_window(act: dict):
    start = datetime.fromisoformat(act["startTimeLocal"].replace(" ", "T")[:19])
    dur = act.get("elapsedDuration") or act.get("duration") or 0
    return start, start + timedelta(seconds=float(dur))


def fetch_openmeteo(lat: float, lon: float, start: datetime, end: datetime, session=None) -> dict:
    """Stundenwerte über die Einheit. Junge Einheiten zuerst über die normale API, ältere über das
    Archiv der Vorhersagen; liefert die erste Quelle nichts, wird die andere versucht."""
    params = {
        "latitude": round(lat, 4), "longitude": round(lon, 4),
        "start_date": start.date().isoformat(), "end_date": end.date().isoformat(),
        "hourly": ",".join(OM_HOURLY), "timezone": "auto",
        "wind_speed_unit": "kmh", "temperature_unit": "celsius", "precipitation_unit": "mm",
    }
    urls = [OM_RECENT, OM_HISTORY] if (date.today() - start.date()).days <= OM_RECENT_DAYS \
        else [OM_HISTORY, OM_RECENT]
    session = session or requests
    last_err = None
    for url in urls:
        try:
            resp = session.get(url, params=params, timeout=20)
            resp.raise_for_status()
            win = openmeteo_window(resp.json().get("hourly") or {}, start, end)
            if any(t is not None for t in win.get("temperature_2m") or []):
                return win
        except Exception as e:
            last_err = e
    if last_err:
        raise last_err
    return {}


def is_outdoor(type_key: str, act: dict) -> bool:
    return (type_key or "") not in INDOOR_TYPES and act.get("startLatitude") is not None \
        and act.get("startLongitude") is not None


def weather_for(g, act: dict) -> dict:
    """Wetter einer Aktivität holen. Rückgabe: Zeile für activity_weather (ohne activity_id)."""
    start, end = _activity_window(act)
    errors, garmin_js, win = [], None, {}
    try:
        win = fetch_openmeteo(act["startLatitude"], act["startLongitude"], start, end)
    except Exception as e:
        errors.append(f"open-meteo: {e}")
    try:
        garmin_js = g.get_activity_weather(act["activityId"]) if g is not None else None
    except Exception as e:
        errors.append(f"garmin: {e}")
    om = summarize_openmeteo(win)
    w = combine(garmin_js, om)
    status = "ok" if om and garmin_js else ("teilweise" if om or garmin_js else "fehler")
    return {**w, "status": status, "garmin": garmin_js, "openmeteo": win or None,
            "error": "; ".join(errors) or None}


def save(store: Store, activity_id: int, row: dict) -> None:
    cols = ("status", "temp_c", "temp_min_c", "temp_max_c", "gefuehlt_c", "feuchte_pct", "wind_kmh",
            "boeen_kmh", "niederschlag_mm", "schnee_cm", "wetter", "extrem", "station", "garmin",
            "openmeteo", "error")
    vals = []
    for c in cols:
        v = row.get(c)
        if c in ("extrem", "garmin", "openmeteo") and v is not None:
            v = json.dumps(v, ensure_ascii=False)
        vals.append(v)
    store.conn.execute(
        f"INSERT INTO activity_weather(activity_id, fetched_at, {', '.join(cols)}) "
        f"VALUES(?, ?, {', '.join('?' * len(cols))}) "
        f"ON CONFLICT(activity_id) DO UPDATE SET fetched_at=excluded.fetched_at, "
        + ", ".join(f"{c}=excluded.{c}" for c in cols),
        (int(activity_id), now_iso(), *vals))
    store.conn.commit()


def _todo(store: Store, limit: int) -> list:
    """Draußen-Aktivitäten ohne Wetter (neueste zuerst), dazu junge und länger fehlgeschlagene neu."""
    today = date.today()
    recent = (today - timedelta(days=REFRESH_DAYS)).isoformat()
    retry = (datetime.now() - timedelta(days=RETRY_ERROR_DAYS)).isoformat()
    rows = store.conn.execute(
        "SELECT a.activity_id, a.type_key, a.data, w.status, w.fetched_at FROM activities a "
        "LEFT JOIN activity_weather w ON w.activity_id = a.activity_id "
        "WHERE json_extract(a.data, '$.startLatitude') IS NOT NULL "
        "AND (w.activity_id IS NULL OR a.start_local >= ? OR (w.status = 'fehler' AND w.fetched_at < ?)) "
        "ORDER BY a.start_local DESC", (recent, retry)).fetchall()
    out = []
    for aid, type_key, data, status, fetched_at in rows:
        try:
            act = json.loads(data)
        except (TypeError, ValueError):
            continue
        if not is_outdoor(type_key, act):
            continue
        # junge Einheit schon nach ihrem Ende geholt und seitdem >6 h vergangen: nicht ständig neu
        if status == "ok" and fetched_at:
            start, end = _activity_window(act)
            try:
                if datetime.fromisoformat(fetched_at).astimezone().replace(tzinfo=None) > end + timedelta(hours=6):
                    continue
            except ValueError:
                pass
        out.append(act)
        if len(out) >= limit:
            break
    return out


def sync(g, store: Store, limit: int = 150) -> int:
    """Wetter für bis zu `limit` Aktivitäten holen (Nachladen der Historie verteilt sich so über
    mehrere Syncs). Rückgabe: Zahl der gespeicherten Einheiten."""
    todo = _todo(store, limit)
    n = 0
    for act in todo:
        try:
            row = weather_for(g, act)
            save(store, int(act["activityId"]), row)
            n += 1
            if row["status"] == "fehler":
                log.warning("Wetter %s: %s", act["activityId"], row.get("error"))
        except Exception as e:
            log.warning("Wetter %s fehlgeschlagen: %s", act.get("activityId"), e)
        time.sleep(PAUSE_S)
    if todo:
        log.info("Wetter: %d Einheiten gespeichert%s", n,
                 " (weitere folgen beim nächsten Sync)" if len(todo) >= limit else "")
    return n
