"""Sensoren für Home Assistant aus der eigenen Datenbank.

Die HA-Integration garmin_coach_proxy fragt GET /ha/state ab und legt für jeden Eintrag in
"sensoren" eine Entität sensor.garmin_coach_<key> an. Welche Sensoren es gibt, steht nur hier:
Neue Sensoren brauchen ein Add-on-Update, aber keinen HA-Neustart.

Jeder Sensor: key, name, state, optional unit, device_class, state_class, icon, attributes.
Tageswerte gelten für heute (Schlaf und HRV = letzte Nacht). Fehlt der Wert für heute noch,
ist der Zustand None (in HA "unbekannt"), das Attribut 'tag' zeigt, worauf sich der Wert bezieht.
"""

from datetime import date, datetime, timezone

import queries
from queries import _clean, _dig, _first_not_none as _first, _loads, _r, connect

STATUS_DE = {
    "PRODUCTIVE": "Produktiv", "MAINTAINING": "Erhaltend", "RECOVERY": "Erholung",
    "UNPRODUCTIVE": "Unproduktiv", "DETRAINING": "Formverlust", "OVERREACHING": "Überlastung",
    "PEAKING": "Höchstform", "STRAINED": "Angespannt", "NO_STATUS": "Kein Status",
}
HRV_DE = {"BALANCED": "Ausgeglichen", "UNBALANCED": "Unausgeglichen", "LOW": "Niedrig", "POOR": "Schlecht",
          "NONE": "Kein Status"}
LEVEL_DE = {"POOR": "Schlecht", "LOW": "Niedrig", "MODERATE": "Mäßig", "HIGH": "Hoch", "PRIME": "Top"}
TE_LABEL_DE = {"AEROBIC_BASE": "Grundlage", "RECOVERY": "Erholung", "TEMPO": "Tempo",
               "LACTATE_THRESHOLD": "Schwelle", "VO2MAX": "VO2max", "ANAEROBIC_CAPACITY": "Anaerobe Kapazität",
               "SPEED": "Sprint", "NO_BENEFIT": "kein Effekt"}


def _status_de(phrase):
    if not phrase:
        return None
    for k, v in STATUS_DE.items():
        if str(phrase).upper().startswith(k):
            return v
    return str(phrase)


def _ts_ms(ms):
    """Garmin-GMT-Millisekunden -> ISO mit Zeitzone (für device_class timestamp)."""
    if not isinstance(ms, (int, float)) or ms <= 0:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat(timespec="seconds")


def _ts_gmt(s):
    """'2026-11-06T04:38:36.43' (GMT ohne Zone) -> ISO mit Zone."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s)[:19]).replace(tzinfo=timezone.utc).isoformat(timespec="seconds")
    except ValueError:
        return None


def _hhmm(minutes):
    if not isinstance(minutes, (int, float)):
        return None
    m = int(minutes) % 1440
    return f"{m // 60:02d}:{m % 60:02d}"


def _min_of(seconds):
    return int(round(seconds / 60)) if isinstance(seconds, (int, float)) else None


def _raw_day(c, day: str) -> dict:
    rows = c.execute("SELECT source, data FROM daily_raw WHERE day=? AND data IS NOT NULL", (day,)).fetchall()
    return {r["source"]: _loads(r["data"]) for r in rows}


def _snapshot(c, source: str):
    row = c.execute("SELECT day, data FROM garmin_snapshots WHERE source=? AND data IS NOT NULL "
                    "ORDER BY day DESC LIMIT 1", (source,)).fetchone()
    return (row["day"], _loads(row["data"])) if row else (None, None)


def S(key, name, state, unit=None, device_class=None, state_class=None, icon=None, attributes=None):
    out = {"key": key, "name": name, "state": state}
    for k, v in (("unit", unit), ("device_class", device_class), ("state_class", state_class),
                 ("icon", icon), ("attributes", _clean(attributes or {}))):
        if v:
            out[k] = v
    return out


M = "measurement"


def _daily_sensors(today: str, row: dict, raw: dict) -> list:
    us, sleep = raw.get("user_summary") or {}, raw.get("sleep") or {}
    dto = _dig(sleep, "dailySleepDTO") or {}
    tag = {"tag": today}
    entries = queries._readiness_entries(raw.get("training_readiness"))
    cur = entries[-1] if entries else {}
    nxt = dto.get("nextSleepNeed") or {}
    need = dto.get("sleepNeed") or {}

    return [
        # --- Bereitschaft -------------------------------------------------------
        S("trainingsbereitschaft", "Trainingsbereitschaft", cur.get("score"),
          "%", None, M, "mdi:gauge", {
              **tag, "stufe": LEVEL_DE.get(cur.get("level"), cur.get("level")),
              "hinweis": cur.get("feedbackShort"),
              "uhrzeit": (cur.get("timestampLocal") or "")[11:16] or None,
              "anlass": queries.READINESS_CONTEXT.get(cur.get("inputContext"), cur.get("inputContext")),
              "erholungszeit_h": queries._h(cur.get("recoveryTime")),
              "faktoren_pct": queries._readiness_factors(cur),
              "morgens": row.get("bereitschaft"), "tagesbilanz": row.get("bereitschaft_delta"),
          }),
        S("trainingsbereitschaft_morgen", "Trainingsbereitschaft am Morgen", row.get("bereitschaft"),
          "%", None, M, "mdi:weather-sunset-up", {
              **tag, "stufe": LEVEL_DE.get(row.get("bereitschaft_stufe"), row.get("bereitschaft_stufe")),
              "hinweis": row.get("bereitschaft_hinweis"), "faktoren_pct": row.get("bereitschaft_faktoren_morgen"),
              "erholungszeit_h": row.get("erholungszeit_morgen_h"),
              "regeneration_nacht": row.get("regeneration_nacht"),
          }),
        S("erholungszeit", "Erholungszeit",
          _first(queries._h(cur.get("recoveryTime")), row.get("erholungszeit_morgen_h")),
          "h", "duration", M, "mdi:timer-sand", tag),
        # --- HRV und Puls -------------------------------------------------------
        S("hrv_nacht", "HRV Nacht", row.get("hrv_nacht"), "ms", None, M, "mdi:heart-pulse", {
            **tag, "basis_von": row.get("hrv_basis_von"), "basis_bis": row.get("hrv_basis_bis"),
            "hoechstwert_5min": _dig(raw.get("hrv"), "hrvSummary", "lastNight5MinHigh"),
        }),
        S("hrv_7_tage", "HRV 7 Tage", row.get("hrv_7tage"), "ms", None, M, "mdi:heart-pulse", tag),
        S("hrv_status", "HRV-Status", HRV_DE.get(row.get("hrv_status"), row.get("hrv_status")),
          icon="mdi:heart-cog", attributes={**tag, "roh": row.get("hrv_status"),
                                            "basis_von": row.get("hrv_basis_von"),
                                            "basis_bis": row.get("hrv_basis_bis")}),
        S("ruhepuls", "Ruhepuls", row.get("ruhepuls"), "bpm", None, M, "mdi:heart", tag),
        S("ruhepuls_7_tage", "Ruhepuls 7 Tage", row.get("ruhepuls_7tage"), "bpm", None, M, "mdi:heart", tag),
        # --- Body Battery und Stress ---------------------------------------------
        S("body_battery", "Body Battery", us.get("bodyBatteryMostRecentValue"), "%", "battery", M, None, {
            **tag, "stand": _ts_gmt(us.get("lastSyncTimestampGMT")),
            "hoechstwert": row.get("body_battery_max"), "tiefstwert": row.get("body_battery_min"),
            "aufgeladen": row.get("body_battery_aufgeladen"), "verbraucht": us.get("bodyBatteryDrainedValue"),
            "beim_aufwachen": row.get("body_battery_aufwachen"), "nacht_plus": row.get("body_battery_nacht_plus"),
        }),
        S("body_battery_hoechstwert", "Body Battery Höchstwert", row.get("body_battery_max"), "%", None, M,
          "mdi:battery-arrow-up", tag),
        S("body_battery_aufgeladen", "Body Battery aufgeladen", row.get("body_battery_aufgeladen"), None, None, M,
          "mdi:battery-charging", tag),
        S("stress", "Stress", row.get("stress_avg"), None, None, M, "mdi:head-heart", {
            **tag, "max": us.get("maxStressLevel"), "hoch_min": row.get("stress_hoch_min"),
            "mittel_min": row.get("stress_mittel_min"), "niedrig_min": row.get("stress_niedrig_min"),
            "ruhe_min": row.get("stress_ruhe_min"),
        }),
        # --- Schlaf ---------------------------------------------------------------
        S("schlafwert", "Schlafwert", row.get("schlafwert"), None, None, M, "mdi:sleep", {
            **tag, "unruhe": row.get("schlaf_unruhe"), "stress_schlaf": row.get("stress_schlaf"),
            "feedback": dto.get("sleepScoreFeedback"),
        }),
        S("schlafdauer", "Schlafdauer", _min_of(dto.get("sleepTimeSeconds")), "min", "duration", M, "mdi:bed", tag),
        S("tiefschlaf", "Tiefschlaf", _min_of(dto.get("deepSleepSeconds")), "min", "duration", M, "mdi:bed", tag),
        S("leichtschlaf", "Leichtschlaf", _min_of(dto.get("lightSleepSeconds")), "min", "duration", M, "mdi:bed",
          tag),
        S("rem_schlaf", "REM-Schlaf", _min_of(dto.get("remSleepSeconds")), "min", "duration", M, "mdi:bed", tag),
        S("wachzeit", "Wach im Schlaf", _min_of(dto.get("awakeSleepSeconds")), "min", "duration", M,
          "mdi:eye-outline", tag),
        S("schlafbedarf", "Schlafbedarf", need.get("actual"), "min", "duration", M, "mdi:bed-clock",
          {**tag, "basis": need.get("baseline"), "feedback": need.get("feedback"),
           "naechste_nacht": nxt.get("actual")}),
        S("schlafbeginn", "Eingeschlafen", _ts_ms(dto.get("sleepStartTimestampGMT")), device_class="timestamp",
          icon="mdi:sleep", attributes=tag),
        S("aufgewacht", "Aufgewacht", _ts_ms(dto.get("sleepEndTimestampGMT")), device_class="timestamp",
          icon="mdi:weather-sunset-up", attributes=tag),
        S("schlafenszeit_empfohlen", "Empfohlene Schlafenszeit", _hhmm(nxt.get("recommendedBedtimeStartMins")),
          icon="mdi:bed-clock", attributes={
              "bis": _hhmm(nxt.get("recommendedBedtimeEndMins")), "fuer_nacht_auf": nxt.get("calendarDate"),
              "schlafbedarf_min": nxt.get("actual"), "feedback": nxt.get("feedback")}),
        S("atem_schlaf", "Atemfrequenz im Schlaf", row.get("atem_schlaf"), "br/min", None, M, "mdi:lungs",
          {**tag, "wach": row.get("atem_wach")}),
        S("hauttemperatur", "Hauttemperatur-Abweichung", row.get("haut_temp_abweichung"), "°C", None, M,
          "mdi:thermometer", tag),
        S("spo2", "SpO2", row.get("spo2_avg"), "%", None, M, "mdi:water-percent",
          {**tag, "minimum": row.get("spo2_min")}),
        # --- Aktivität des Tages --------------------------------------------------
        S("schritte", "Schritte", row.get("schritte"), "Schritte", None, "total_increasing", "mdi:walk", tag),
        S("intensitaetsminuten", "Intensitätsminuten",
          None if row.get("intensitaetsminuten_moderat") is None else
          (row.get("intensitaetsminuten_moderat") or 0) + 2 * (row.get("intensitaetsminuten_intensiv") or 0),
          "min", None, M, "mdi:run", {**tag, "moderat": row.get("intensitaetsminuten_moderat"),
                                      "intensiv": row.get("intensitaetsminuten_intensiv")}),
    ]


def _training_sensors(c, row: dict) -> list:
    out = []
    ts_day = c.execute("SELECT MAX(day) d FROM daily_raw WHERE source='training_status' AND data IS NOT NULL"
                       ).fetchone()["d"]
    ts = queries._training_status(_loads(c.execute(
        "SELECT data FROM daily_raw WHERE source='training_status' AND day=?", (ts_day,)).fetchone()["data"])
    ) if ts_day else {}
    ratio = ts.get("last_verhaeltnis")
    out.append(S("trainingsstatus", "Trainingsstatus", _status_de(ts.get("status")), icon="mdi:chart-line",
                 attributes={"tag": ts_day, "roh": ts.get("status"), "akute_last": ts.get("akute_last"),
                             "chronische_last": ts.get("chronische_last"), "last_verhaeltnis": ratio,
                             "last_aerob_niedrig": ts.get("last_aerob_niedrig"),
                             "last_aerob_hoch": ts.get("last_aerob_hoch"), "last_anaerob": ts.get("last_anaerob"),
                             "hitze_akklimatisierung_pct": ts.get("hitze_akklimatisierung_pct")}))
    out.append(S("akute_last", "Akute Last", ts.get("akute_last"), None, None, M, "mdi:weight-lifter",
                 {"tag": ts_day, "chronisch": ts.get("chronische_last"), "verhaeltnis": ratio}))
    out.append(S("vo2max", "VO2max", ts.get("vo2max") or row.get("vo2max"), "ml/kg/min", None, M,
                 "mdi:lungs", {"tag": ts_day}))

    lt_day, lt = _snapshot(c, "lactate_threshold")
    lts = queries._lactate_summary(lt) if lt else {}
    out.append(S("laktatschwelle_hf", "Laktatschwelle Herzfrequenz", lts.get("schwelle_hf"), "bpm", None, M,
                 "mdi:heart-flash", {"pace": lts.get("schwelle_pace"), "ermittelt_am": lts.get("ermittelt_am")}))
    out.append(S("laktatschwelle_pace", "Laktatschwelle Pace", lts.get("schwelle_pace"), icon="mdi:speedometer",
                 attributes={"tempo_ms": lts.get("schwelle_tempo_ms"), "hf": lts.get("schwelle_hf"),
                             "ermittelt_am": lts.get("ermittelt_am")}))
    out.append(S("ftp_laufen", "FTP Laufen", lts.get("ftp_watt"), "W", "power", M, "mdi:flash",
                 {"watt_pro_kg": lts.get("watt_pro_kg")}))

    _, es = _snapshot(c, "endurance_score")
    out.append(S("endurance_score", "Endurance Score", _dig(es, "enduranceScoreDTO", "overallScore"), None, None,
                 M, "mdi:infinity", {"klasse": _dig(es, "enduranceScoreDTO", "classification")}))
    _, hs = _snapshot(c, "hill_score")
    h0 = (hs or {}).get("hillScoreDTOList") or [{}]
    h0 = h0[0] if isinstance(h0, list) and h0 else {}
    out.append(S("hill_score", "Hill Score", h0.get("overallScore"), None, None, M, "mdi:image-filter-hdr",
                 {"kraft": h0.get("strengthScore"), "ausdauer": h0.get("enduranceScore")}))

    rp_day, rp = _snapshot(c, "race_predictions")
    for key, name, field in (("prognose_5k", "Prognose 5 km", "time5K"), ("prognose_10k", "Prognose 10 km", "time10K"),
                             ("prognose_halbmarathon", "Prognose Halbmarathon", "timeHalfMarathon"),
                             ("prognose_marathon", "Prognose Marathon", "timeMarathon")):
        sec = (rp or {}).get(field)
        txt = f"{int(sec) // 3600}:{int(sec) % 3600 // 60:02d}:{int(sec) % 60:02d}" if sec else None
        out.append(S(key, name, txt, icon="mdi:flag-checkered", attributes={"sekunden": sec, "tag": rp_day}))

    _, rt = _snapshot(c, "running_tolerance")
    rt0 = sorted(rt, key=lambda e: e.get("calendarDate") or "")[-1] if isinstance(rt, list) and rt else {}
    load, tol = rt0.get("acuteImpactLoad"), rt0.get("acuteTolerance")
    out.append(S("running_tolerance", "Running Tolerance", _r(load / tol * 100, 0) if load and tol else None,
                 "%", None, M, "mdi:shoe-print", {"tag": rt0.get("calendarDate"), "last": load, "toleranz": tol,
                                                  "hinweis": rt0.get("runningToleranceFeedBackPhrase")}))
    return out


def _weight_sensor(c) -> dict:
    row = c.execute("SELECT day, data FROM daily_raw WHERE source='weight' AND data IS NOT NULL "
                    "ORDER BY day DESC LIMIT 1").fetchone()
    w = queries._weight_out(_loads(row["data"])) if row else {}
    return S("gewicht", "Gewicht", w.get("gewicht_kg"), "kg", "weight", M, "mdi:scale-bathroom",
             {"tag": row["day"] if row else None, "bmi": w.get("bmi"), "koerperfett_pct": w.get("koerperfett_pct"),
              "muskelmasse_kg": w.get("muskelmasse_kg")})


def _activity_sensor(c) -> dict:
    acts = queries.list_activities(limit=1)
    a = acts[0] if acts else {}
    extra = {}
    if a:
        js = _loads(c.execute("SELECT data FROM activities WHERE activity_id=?",
                              (a["activity_id"],)).fetchone()["data"]) or {}
        extra = {"kalorien": _r(js.get("calories"), 0), "abstieg_m": _r(js.get("elevationLoss"), 0),
                 "ort": js.get("locationName")}
        diary = [queries._diary_short(dict(d)) for d in c.execute(
            "SELECT * FROM diary WHERE activity_id=? ORDER BY id", (a["activity_id"],))]
        if diary:
            extra["tagebuch"] = diary
    mins = a.get("minuten")
    start = a.get("start")
    return S("letzte_aktivitaet", "Letzte Aktivität", a.get("name"), icon="mdi:run-fast", attributes={
        **{k: v for k, v in a.items() if k != "name"},
        "start": start,
        "dauer": f"{int(mins // 60)}:{int(mins % 60):02d}:{int(round(mins * 60)) % 60:02d}" if mins else None,
        "schwerpunkt": TE_LABEL_DE.get(a.get("te_label"), a.get("te_label")),
        "ist_lauf": a.get("sport_gruppe") == "laufen" if a else None,
        **extra,
    })


def _diary_sensor(c) -> dict:
    row = c.execute("SELECT * FROM diary ORDER BY day DESC, id DESC LIMIT 1").fetchone()
    d = dict(row) if row else {}
    return S("tagebuch", "Tagebuch", d.get("day"), icon="mdi:notebook-edit", attributes={
        "id": d.get("id"), "activity_id": d.get("activity_id"), "beine": d.get("beine"),
        "energie": d.get("energie"), "infekt": d.get("infekt"), "schmerz": d.get("schmerz"),
        "schmerz_staerke": d.get("schmerz_staerke"), "text": d.get("text"),
    })


def _sync_sensor(c) -> dict:
    row = c.execute("SELECT finished_at, kind, message FROM sync_log WHERE status='ok' "
                    "ORDER BY id DESC LIMIT 1").fetchone()
    return S("letzter_sync", "Letzter Sync", row["finished_at"] if row else None, device_class="timestamp",
             icon="mdi:cloud-sync", attributes={"art": row["kind"], "meldung": row["message"]} if row else None)


def build() -> dict:
    today = date.today().isoformat()
    rows = queries.daily_metrics(today, today)
    row = rows[0] if rows and rows[0].get("tag") == today else {}
    with connect() as c:
        raw = _raw_day(c, today)
        sensors = _daily_sensors(today, row, raw)
        sensors += _training_sensors(c, row)
        sensors += [_weight_sensor(c), _activity_sensor(c), _diary_sensor(c), _sync_sensor(c)]
    return {"erzeugt": datetime.now(timezone.utc).isoformat(timespec="seconds"), "tag": today,
            "sensoren": sensors}
