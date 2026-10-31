"""Berechnete Kennzahlen pro Einheit."""

from statistics import mean, pstdev

MOVING_SPEED = 1.0  # m/s, darunter gilt man als stehend/gehend
MIN_DECOUPLING_S = 20 * 60


def _ef(recs):
    """Effizienz: Meter pro Minute je Herzschlag."""
    pairs = [(r["speed"], r["hr"]) for r in recs if r.get("speed") and r.get("hr")]
    if not pairs:
        return None
    return mean(s for s, _ in pairs) * 60 / mean(h for _, h in pairs)


def decoupling(records):
    """Pa:HR-Entkopplung in %: wie stark die Effizienz von der ersten zur
    zweiten Hälfte abnimmt. Unter ~5 % gilt als gute aerobe Ausdauer.
    Nur sinnvoll für gleichmäßige Läufe, deshalb liefern wir zusätzlich die
    Tempo-Schwankung (CV) mit."""
    moving = [r for r in records if (r.get("speed") or 0) >= MOVING_SPEED and r.get("hr")]
    if len(moving) < MIN_DECOUPLING_S:
        return None, None
    half = len(moving) // 2
    ef1, ef2 = _ef(moving[:half]), _ef(moving[half:])
    speeds = [r["speed"] for r in moving]
    cv = pstdev(speeds) / mean(speeds) * 100
    if not ef1 or not ef2:
        return None, round(cv, 1)
    return round((ef1 - ef2) / ef1 * 100, 2), round(cv, 1)


def interval_target_hit(laps):
    """Anteil der Belastungsrunden, deren Tempo im Zielbereich lag."""
    checked = hit = 0
    for lp in laps:
        lo, hi, v = lp.get("target_speed_low"), lp.get("target_speed_high"), lp.get("avg_speed")
        if lp.get("intensity") != "active" or not (lo and hi and v):
            continue
        if (lp.get("distance_m") or 0) < 200:  # Rundenreste ignorieren
            continue
        checked += 1
        hit += lo <= v <= hi
    return (round(hit / checked * 100, 1), checked) if checked else (None, 0)


def hr_recovery(laps, records):
    """Mittlerer Pulsabfall in den ersten 60 s jeder Erholungsrunde."""
    by_t = {r["t"]: r.get("hr") for r in records if r.get("hr")}
    if not by_t or not laps:
        return None
    drops = []
    for lp in laps:
        if lp.get("intensity") != "recovery" or lp.get("start_t") is None:
            continue
        start = lp["start_t"]
        hr_start = next((by_t[t] for t in range(start, start - 5, -1) if t in by_t), None)
        hr_60 = next((by_t[t] for t in range(start + 60, start + 65) if t in by_t), None)
        if hr_start and hr_60:
            drops.append(hr_start - hr_60)
    return round(mean(drops), 1) if drops else None


def compute(parsed: dict) -> dict:
    s, laps, recs = parsed["session"], parsed["laps"], parsed["records"]
    dec, cv = decoupling(recs)
    hit_pct, hit_n = interval_target_hit(laps)
    return {
        "efficiency": round(_ef([r for r in recs if (r.get("speed") or 0) >= MOVING_SPEED]) or 0, 3) or None,
        "decoupling_pct": dec,
        "speed_cv_pct": cv,
        "target_hit_pct": hit_pct,
        "target_laps": hit_n,
        "hr_recovery_60s": hr_recovery(laps, recs),
        "hr_samples": sum(1 for r in recs if r.get("hr")),
    }
