"""FIT-Dateien auslesen: Gesamtwerte, Runden, Sekundenwerte und Zusatzdaten.

Einheiten wie in der FIT-Spezifikation, nur wo nötig umgerechnet:
- Tempo in m/s, Distanz in m, Zeiten in s
- Kadenz beim Laufen/Gehen in Schritten pro Minute (FIT speichert pro Fuß)
- Schrittlänge und vertikale Bewegung in mm, Bodenkontaktzeit in ms
"""

from datetime import datetime

import fitdecode

# Sportarten, bei denen FIT die Kadenz pro Fuß speichert
STEP_SPORTS = {"running", "walking", "hiking", "trail_running", "floor_climbing"}


def _iso(v):
    return v.isoformat() if isinstance(v, datetime) else v


def _jsonable(d: dict) -> dict:
    out = {}
    for k, v in d.items():
        if k.startswith("unknown"):
            continue
        if isinstance(v, datetime):
            v = v.isoformat()
        elif isinstance(v, tuple):
            v = [x.isoformat() if isinstance(x, datetime) else x for x in v]
        out[k] = v
    return out


def _spm(whole, fractional, is_steps: bool):
    if whole is None:
        return None
    val = whole + (fractional or 0)
    return round(val * 2, 1) if is_steps else round(val, 1)


def parse(path: str) -> dict:
    """Liest eine FIT-Datei und liefert alle relevanten Nachrichten als Dicts."""
    msgs = {
        "session": [], "lap": [], "record": [], "time_in_zone": [],
        "workout_step": [], "length": [], "set": [], "device_info": [], "split": [],
    }
    with fitdecode.FitReader(path) as fr:
        for frame in fr:
            if not isinstance(frame, fitdecode.FitDataMessage) or frame.name not in msgs:
                continue
            msgs[frame.name].append(
                {f.name: f.value for f in frame.fields if f.value is not None}
            )

    if not msgs["session"]:
        raise ValueError("keine Session in der FIT-Datei")
    s = msgs["session"][0]
    sport = str(s.get("sport") or "")
    is_steps = sport in STEP_SPORTS
    start = s.get("start_time")

    # Pulszonen der gesamten Einheit
    tiz = next((z for z in msgs["time_in_zone"] if z.get("reference_mesg") == "session"), {})

    devices = sorted({
        f"{d.get('manufacturer')}:{d.get('garmin_product') or d.get('product')}"
        for d in msgs["device_info"] if d.get("manufacturer")
    })

    session = {
        "sport": sport,
        "sub_sport": str(s.get("sub_sport") or ""),
        "start_time": _iso(start),
        "distance_m": s.get("total_distance"),
        "timer_s": s.get("total_timer_time"),
        "elapsed_s": s.get("total_elapsed_time"),
        "avg_speed": s.get("enhanced_avg_speed") or s.get("avg_speed"),
        "max_speed": s.get("enhanced_max_speed") or s.get("max_speed"),
        "avg_hr": s.get("avg_heart_rate"),
        "max_hr": s.get("max_heart_rate"),
        "min_hr": s.get("min_heart_rate"),
        "avg_cadence": _spm(s.get("avg_running_cadence") or s.get("avg_cadence"),
                            s.get("avg_fractional_cadence"), is_steps),
        "avg_power": s.get("avg_power"),
        "normalized_power": s.get("normalized_power"),
        "avg_step_length_mm": s.get("avg_step_length"),
        "avg_gct_ms": s.get("avg_stance_time"),
        "avg_vo_mm": s.get("avg_vertical_oscillation"),
        "avg_vr_pct": s.get("avg_vertical_ratio"),
        "avg_resp": s.get("enhanced_avg_respiration_rate"),
        "ascent_m": s.get("total_ascent"),
        "descent_m": s.get("total_descent"),
        "avg_temp": s.get("avg_temperature"),
        "calories": s.get("total_calories"),
        "te_aerobic": s.get("total_training_effect"),
        "te_anaerobic": s.get("total_anaerobic_training_effect"),
        "training_load": s.get("training_load_peak"),
        "rpe": s.get("workout_rpe"),
        "feel": s.get("workout_feel"),
        "threshold_hr": tiz.get("threshold_heart_rate"),
        "resting_hr": tiz.get("resting_heart_rate"),
        "hr_zone_bounds": list(tiz.get("hr_zone_high_boundary") or []) or None,
        "time_in_hr_zone": [round(x, 1) for x in (tiz.get("time_in_hr_zone") or [])] or None,
        "devices": devices,
        "data": _jsonable(s),
    }

    steps = []
    for st in msgs["workout_step"]:
        steps.append({
            "step_index": st.get("message_index", len(steps)),
            "intensity": str(st.get("intensity") or ""),
            "duration_type": str(st.get("duration_type") or ""),
            "duration_value": st.get("duration_time") or st.get("duration_distance")
                              or st.get("duration_step"),
            "target_type": str(st.get("target_type") or ""),
            "target_speed_low": st.get("custom_target_speed_low"),
            "target_speed_high": st.get("custom_target_speed_high"),
            "target_hr_low": st.get("custom_target_heart_rate_low"),
            "target_hr_high": st.get("custom_target_heart_rate_high"),
            "repeat_steps": st.get("repeat_steps"),
            "data": _jsonable(st),
        })
    step_by_idx = {st["step_index"]: st for st in steps}

    laps = []
    for i, lp in enumerate(msgs["lap"]):
        step = step_by_idx.get(lp.get("wkt_step_index"))
        lap_start = lp.get("start_time")
        laps.append({
            "lap_index": i,
            "start_time": _iso(lap_start),
            "start_t": int((lap_start - start).total_seconds()) if lap_start and start else None,
            "intensity": str(lp.get("intensity") or ""),
            "lap_trigger": str(lp.get("lap_trigger") or ""),
            "wkt_step_index": lp.get("wkt_step_index"),
            "target_speed_low": step and step["target_speed_low"],
            "target_speed_high": step and step["target_speed_high"],
            "distance_m": lp.get("total_distance"),
            "timer_s": lp.get("total_timer_time"),
            "elapsed_s": lp.get("total_elapsed_time"),
            "avg_speed": lp.get("enhanced_avg_speed") or lp.get("avg_speed"),
            "max_speed": lp.get("enhanced_max_speed") or lp.get("max_speed"),
            "avg_hr": lp.get("avg_heart_rate"),
            "max_hr": lp.get("max_heart_rate"),
            "avg_cadence": _spm(lp.get("avg_running_cadence") or lp.get("avg_cadence"),
                                lp.get("avg_fractional_cadence"), is_steps),
            "max_cadence": _spm(lp.get("max_running_cadence") or lp.get("max_cadence"),
                                lp.get("max_fractional_cadence"), is_steps),
            "avg_step_length_mm": lp.get("avg_step_length"),
            "avg_gct_ms": lp.get("avg_stance_time"),
            "gct_balance_pct": lp.get("avg_stance_time_balance"),
            "avg_vo_mm": lp.get("avg_vertical_oscillation"),
            "avg_vr_pct": lp.get("avg_vertical_ratio"),
            "avg_power": lp.get("avg_power"),
            "max_power": lp.get("max_power"),
            "normalized_power": lp.get("normalized_power"),
            "avg_resp": lp.get("enhanced_avg_respiration_rate"),
            "ascent_m": lp.get("total_ascent"),
            "descent_m": lp.get("total_descent"),
            "avg_temp": lp.get("avg_temperature"),
            "calories": lp.get("total_calories"),
            "data": _jsonable(lp),
        })

    # Sekundenwerte relativ zum Start der Einheit
    records = {}
    t0 = start or (msgs["record"][0].get("timestamp") if msgs["record"] else None)
    for r in msgs["record"]:
        ts = r.get("timestamp")
        if ts is None or t0 is None:
            continue
        t = int((ts - t0).total_seconds())
        records[t] = {
            "t": t,
            "hr": r.get("heart_rate"),
            "speed": r.get("enhanced_speed") or r.get("speed"),
            "distance_m": r.get("distance"),
            "altitude_m": r.get("enhanced_altitude") or r.get("altitude"),
            "cadence": _spm(r.get("cadence"), r.get("fractional_cadence"), is_steps),
            "power": r.get("power"),
            "gct_ms": r.get("stance_time"),
            "gct_balance_pct": r.get("stance_time_balance"),
            "vo_mm": r.get("vertical_oscillation"),
            "vr_pct": r.get("vertical_ratio"),
            "step_length_mm": r.get("step_length"),
            "resp": r.get("enhanced_respiration_rate") or r.get("respiration_rate"),
            "temp": r.get("temperature"),
        }

    lengths = [{
        "length_index": ln.get("message_index", i),
        "start_time": _iso(ln.get("start_time")),
        "length_type": str(ln.get("length_type") or ""),
        "swim_stroke": str(ln.get("swim_stroke") or ""),
        "timer_s": ln.get("total_timer_time"),
        "strokes": ln.get("total_strokes"),
        "cadence": ln.get("avg_swimming_cadence"),
        "avg_speed": ln.get("avg_speed"),
    } for i, ln in enumerate(msgs["length"])]

    sets = [{
        "set_index": st.get("message_index", i),
        "start_time": _iso(st.get("start_time")),
        "set_type": str(st.get("set_type") or ""),
        "duration_s": st.get("duration"),
        "repetitions": st.get("repetitions"),
        "weight": st.get("weight"),
        "data": _jsonable(st),
    } for i, st in enumerate(msgs["set"])]

    return {
        "session": session,
        "laps": laps,
        "records": [records[t] for t in sorted(records)],
        "workout_steps": steps,
        "lengths": lengths,
        "sets": sets,
    }
