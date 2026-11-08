"""Strukturierte Trainings für die Uhr: anlegen, in den Garmin-Kalender legen, auflisten, löschen.

Der Coach beschreibt eine Einheit als einfache Schrittliste; dieses Modul baut daraus das
Workout-JSON von Garmin Connect, lädt es hoch und plant es auf ein Datum. Die Uhr holt es sich
beim nächsten Sync.

Sicherheit: Alle Trainings des Coaches tragen das Präfix PREFIX im Namen und MARKER in der
Beschreibung. Löschen und Ersetzen funktionieren nur bei solchen Trainings, nie bei Brunos
eigenen oder denen anderer Apps.

Schrittformat (Liste von dicts):
    {"typ": "aufwaermen" | "lauf" | "intervall" | "erholung" | "pause" | "abwaermen",
     genau eins von: "dauer_min": 8 | "distanz_km": 2 | "bis_taste": true,
     optional: "pace": "4:00-4:10" (min/km), "hf": "150-160" (bpm), "hf_zone": 2, "hinweis": "..."}
    Wiederholung: {"wiederholen": 4, "schritte": [ ...Schritte... ]}
Mit pace und hf zugleich ist Pace das Hauptziel und HF das Zweitziel.
"""

import logging
import re
import threading
from datetime import date, datetime, timedelta

log = logging.getLogger("garmin_coach.workouts")

PREFIX = "Coach: "
MARKER = "[garmin-coach]"
MAX_STEPS = 50
DEFAULT_PACE_S = 330  # Annahme für die Dauer-Schätzung bei Distanz ohne Pace (5:30 min/km)

# --- Garmin-Client (wird beim Login aus main.py gesetzt) -----------------------

_client = None
_client_lock = threading.Lock()  # Workout-Aufrufe nacheinander, nie parallel


def set_client(g) -> None:
    global _client
    _client = g


def _garmin():
    if _client is None:
        raise RuntimeError("Noch nicht bei Garmin angemeldet (Add-on startet gerade oder Login fehlgeschlagen)")
    return _client


# --- Bausteine ------------------------------------------------------------------

STEP_TYPES = {
    "aufwaermen": (1, "warmup"),
    "abwaermen": (2, "cooldown"),
    "intervall": (3, "interval"),
    "erholung": (4, "recovery"),
    "pause": (5, "rest"),
    "lauf": (3, "interval"),  # gleichmäßiger Lauf als ein Belastungsschritt
}
ALIASES = {"warmup": "aufwaermen", "einlaufen": "aufwaermen", "cooldown": "abwaermen",
           "auslaufen": "abwaermen", "interval": "intervall", "recovery": "erholung",
           "trab": "erholung", "rest": "pause", "run": "lauf", "dauerlauf": "lauf"}

COND_TIME = {"conditionTypeId": 2, "conditionTypeKey": "time", "displayOrder": 2, "displayable": True}
COND_DIST = {"conditionTypeId": 3, "conditionTypeKey": "distance", "displayOrder": 3, "displayable": True}
COND_LAP = {"conditionTypeId": 1, "conditionTypeKey": "lap.button", "displayOrder": 1, "displayable": True}
COND_ITER = {"conditionTypeId": 7, "conditionTypeKey": "iterations", "displayOrder": 7, "displayable": False}

T_NONE = {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target", "displayOrder": 1}
T_HR = {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone", "displayOrder": 4}
T_PACE = {"workoutTargetTypeId": 6, "workoutTargetTypeKey": "pace.zone", "displayOrder": 6}

SPORT_RUN = {"sportTypeId": 1, "sportTypeKey": "running", "displayOrder": 1}
METER = {"unitId": 1, "unitKey": "meter", "factor": 100.0}


def _pace_s(text: str) -> int:
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", text)
    if not m or int(m.group(2)) >= 60:
        raise ValueError(f"Pace '{text}' nicht lesbar, Format m:ss (z. B. 4:05)")
    s = int(m.group(1)) * 60 + int(m.group(2))
    if not 150 <= s <= 900:
        raise ValueError(f"Pace {text} min/km ist unplausibel (2:30–15:00 erlaubt)")
    return s


def _pace_range(text: str) -> tuple[int, int]:
    """'4:00-4:10' -> (240, 250) Sekunden pro km (schnell, langsam). Einzelwert: ±5 s."""
    parts = re.split(r"\s*[-–]\s*", str(text).strip())
    if len(parts) == 1:
        s = _pace_s(parts[0])
        return s - 5, s + 5
    if len(parts) != 2:
        raise ValueError(f"Pace-Bereich '{text}' nicht lesbar, Format 4:00-4:10")
    a, b = sorted((_pace_s(parts[0]), _pace_s(parts[1])))
    return a, b


def _hr_range(text: str) -> tuple[int, int]:
    parts = re.split(r"\s*[-–]\s*", str(text).strip())
    try:
        vals = [int(p) for p in parts]
    except ValueError:
        raise ValueError(f"HF-Bereich '{text}' nicht lesbar, Format 150-160") from None
    if len(vals) == 1:
        vals = [vals[0] - 3, vals[0] + 3]
    if len(vals) != 2:
        raise ValueError(f"HF-Bereich '{text}' nicht lesbar, Format 150-160")
    a, b = sorted(vals)
    if not (60 <= a and b <= 220):
        raise ValueError(f"HF-Bereich {text} ist unplausibel (60–220 erlaubt)")
    return a, b


def _mps(sec_per_km: int) -> float:
    return round(1000.0 / sec_per_km, 7)


class _Builder:
    def __init__(self):
        self.order = 0
        self.child = 0
        self.est_s = 0.0
        self.est_m = 0.0
        self.count = 0

    def executable(self, st: dict, child_id: int | None, factor: int) -> dict:
        if not isinstance(st, dict):
            raise ValueError(f"Schritt muss ein Objekt sein, nicht {st!r}")
        typ = str(st.get("typ") or "").strip().lower()
        typ = ALIASES.get(typ, typ)
        if typ not in STEP_TYPES:
            raise ValueError(f"Unbekannter Schritt-Typ '{st.get('typ')}' (erlaubt: {', '.join(STEP_TYPES)})")
        type_id, type_key = STEP_TYPES[typ]
        self.order += 1
        self.count += 1
        if self.count > MAX_STEPS:
            raise ValueError(f"Zu viele Schritte (max. {MAX_STEPS})")
        out = {"type": "ExecutableStepDTO", "stepOrder": self.order,
               "stepType": {"stepTypeId": type_id, "stepTypeKey": type_key, "displayOrder": type_id}}
        if child_id is not None:
            out["childStepId"] = child_id

        pace = _pace_range(st["pace"]) if st.get("pace") else None
        ends = [k for k in ("dauer_min", "distanz_km", "bis_taste") if st.get(k)]
        if len(ends) != 1:
            raise ValueError(f"Schritt {self.order} ({typ}): genau eins von dauer_min, distanz_km, bis_taste angeben")
        if ends[0] == "dauer_min":
            secs = float(st["dauer_min"]) * 60
            if not 5 <= secs <= 6 * 3600:
                raise ValueError(f"Schritt {self.order}: Dauer {st['dauer_min']} min unplausibel")
            out["endCondition"], out["endConditionValue"] = COND_TIME, round(secs)
            self.est_s += secs * factor
            if pace:
                self.est_m += secs / (sum(pace) / 2) * 1000 * factor
        elif ends[0] == "distanz_km":
            meters = float(st["distanz_km"]) * 1000
            if not 10 <= meters <= 150_000:
                raise ValueError(f"Schritt {self.order}: Distanz {st['distanz_km']} km unplausibel")
            out["endCondition"], out["endConditionValue"] = COND_DIST, round(meters)
            out["preferredEndConditionUnit"] = METER
            self.est_m += meters * factor
            self.est_s += meters / 1000 * (sum(pace) / 2 if pace else DEFAULT_PACE_S) * factor
        else:
            out["endCondition"] = COND_LAP

        hr = _hr_range(st["hf"]) if st.get("hf") else None
        zone = st.get("hf_zone")
        if hr and zone:
            raise ValueError(f"Schritt {self.order}: entweder hf oder hf_zone, nicht beides")
        if zone is not None and not 1 <= int(zone) <= 5:
            raise ValueError(f"Schritt {self.order}: hf_zone muss 1–5 sein")

        hr_target = None
        if hr:
            hr_target = (T_HR, float(hr[0]), float(hr[1]), None)
        elif zone:
            hr_target = (T_HR, None, None, int(zone))

        primary = None
        if pace:
            # Garmin: targetValueOne = langsameres Tempo (kleinere m/s), targetValueTwo = schnelleres
            primary = (T_PACE, _mps(pace[1]), _mps(pace[0]), None)
            secondary = hr_target
        else:
            primary, secondary = hr_target, None

        if primary:
            out["targetType"] = primary[0]
            if primary[3] is not None:
                out["zoneNumber"] = primary[3]
            else:
                out["targetValueOne"], out["targetValueTwo"] = primary[1], primary[2]
        else:
            out["targetType"] = T_NONE
        if secondary:
            out["secondaryTargetType"] = secondary[0]
            if secondary[3] is not None:
                out["secondaryZoneNumber"] = secondary[3]
            else:
                out["secondaryTargetValueOne"], out["secondaryTargetValueTwo"] = secondary[1], secondary[2]

        if st.get("hinweis"):
            out["description"] = str(st["hinweis"])[:500]
        return out

    def steps(self, steps: list, child_id: int | None = None, factor: int = 1, depth: int = 0) -> list:
        if not isinstance(steps, list) or not steps:
            raise ValueError("schritte muss eine nicht leere Liste sein")
        out = []
        for st in steps:
            if isinstance(st, dict) and "wiederholen" in st:
                if depth >= 2:
                    raise ValueError("Wiederholungen höchstens zwei Ebenen tief verschachteln")
                n = int(st["wiederholen"])
                if not 1 <= n <= 50:
                    raise ValueError(f"wiederholen={n} unplausibel (1–50)")
                self.order += 1
                self.child += 1
                cid = self.child
                group = {"type": "RepeatGroupDTO", "stepOrder": self.order,
                         "stepType": {"stepTypeId": 6, "stepTypeKey": "repeat", "displayOrder": 6},
                         "childStepId": cid, "numberOfIterations": n,
                         "endCondition": COND_ITER, "endConditionValue": float(n),
                         "smartRepeat": False}
                group["workoutSteps"] = self.steps(st.get("schritte"), cid, factor * n, depth + 1)
                if st.get("letzte_pause_weglassen"):
                    group["skipLastRestStep"] = True
                out.append(group)
            else:
                out.append(self.executable(st, child_id, factor))
        return out


def build(name: str, schritte: list, beschreibung: str | None = None) -> dict:
    """Workout-JSON für Garmin Connect bauen (ohne Hochladen)."""
    name = (name or "").strip()
    if not name:
        raise ValueError("name fehlt")
    full_name = name if name.startswith(PREFIX) else PREFIX + name
    if len(full_name) > 80:
        raise ValueError("name zu lang (max. ca. 70 Zeichen)")
    b = _Builder()
    steps = b.steps(schritte)
    descr = ((beschreibung or "").strip() + " " + MARKER).strip()[:1000]
    return {
        "workoutName": full_name,
        "description": descr,
        "sportType": SPORT_RUN,
        "estimatedDurationInSecs": int(b.est_s) or None,
        "estimatedDistanceInMeters": round(b.est_m) or None,
        "workoutSegments": [{"segmentOrder": 1, "sportType": SPORT_RUN, "workoutSteps": steps}],
    }


def _strip_none(o):
    if isinstance(o, dict):
        return {k: _strip_none(v) for k, v in o.items() if v is not None}
    if isinstance(o, list):
        return [_strip_none(v) for v in o]
    return o


# --- Lesbare Zusammenfassung -----------------------------------------------------

def _fmt_pace(mps: float) -> str:
    s = round(1000 / mps)
    return f"{s // 60}:{s % 60:02d}"


def describe_steps(steps: list, indent: str = "") -> list[str]:
    """Garmin-Schritte als kurze Textzeilen (für Vorschau und Liste)."""
    lines = []
    for st in steps or []:
        if st.get("type") == "RepeatGroupDTO":
            lines.append(f"{indent}{st.get('numberOfIterations')}× (")
            lines += describe_steps(st.get("workoutSteps"), indent + "  ")
            lines.append(f"{indent})")
            continue
        key = (st.get("stepType") or {}).get("stepTypeKey", "?")
        cond = (st.get("endCondition") or {}).get("conditionTypeKey")
        val = st.get("endConditionValue")
        if cond == "time" and val:
            amount = f"{val / 60:g} min"
        elif cond == "distance" and val:
            amount = f"{val / 1000:g} km"
        else:
            amount = "bis Runden-Taste"
        targets = []
        for prefix in ("", "secondary"):
            tkey = "targetType" if not prefix else "secondaryTargetType"
            tt = (st.get(tkey) or {}).get("workoutTargetTypeKey")
            one = st.get("targetValueOne" if not prefix else "secondaryTargetValueOne")
            two = st.get("targetValueTwo" if not prefix else "secondaryTargetValueTwo")
            zone = st.get("zoneNumber" if not prefix else "secondaryZoneNumber")
            if tt == "pace.zone" and one and two:
                targets.append(f"{_fmt_pace(two)}–{_fmt_pace(one)} min/km")
            elif tt == "heart.rate.zone" and zone:
                targets.append(f"HF-Zone {zone}")
            elif tt == "heart.rate.zone" and one and two:
                targets.append(f"HF {one:g}–{two:g}")
        lines.append(f"{indent}{key} {amount}" + (f" @ {', '.join(targets)}" if targets else ""))
    return lines


# --- Garmin-Aufrufe --------------------------------------------------------------

def _is_coach(w: dict) -> bool:
    return str(w.get("workoutName") or "").startswith(PREFIX) or MARKER in str(w.get("description") or "")


def _check_day(day: str) -> str:
    try:
        d = date.fromisoformat(day)
    except (TypeError, ValueError):
        raise ValueError("day im Format YYYY-MM-DD angeben") from None
    today = date.today()
    if d < today:
        raise ValueError(f"{day} liegt in der Vergangenheit")
    if d > today + timedelta(days=60):
        raise ValueError(f"{day} liegt mehr als 60 Tage in der Zukunft")
    return d.isoformat()


def list_scheduled(start_date: str | None = None, end_date: str | None = None) -> dict:
    start = start_date or date.today().isoformat()
    end = end_date or (date.fromisoformat(start) + timedelta(days=13)).isoformat()
    with _client_lock:
        items = _garmin().get_scheduled_workouts_by_period(start, end) or []
    out = []
    for s in items:
        w = s.get("workout") or {}
        segs = w.get("workoutSegments") or []
        out.append({
            "tag": s.get("calendarDate"),
            "name": w.get("workoutName"),
            "workout_id": w.get("workoutId"),
            "schedule_id": s.get("workoutScheduleId"),
            "von_coach": _is_coach(w),
            "anbieter": w.get("workoutProvider"),
            "sport": (w.get("sportType") or {}).get("sportTypeKey"),
            "erledigt_mit_aktivitaet": s.get("associatedActivityId"),
            "schritte": describe_steps(segs[0].get("workoutSteps")) if segs else None,
        })
    out.sort(key=lambda x: (x["tag"] or "", x["name"] or ""))
    return {"von": start, "bis": end, "eintraege": out}


def _delete_coach_workout(g, workout_id: int) -> dict:
    w = g.get_workout_by_id(workout_id)
    if not w:
        raise ValueError(f"Training {workout_id} nicht gefunden")
    if not _is_coach(w):
        raise PermissionError(f"'{w.get('workoutName')}' stammt nicht vom Coach und wird nicht angefasst")
    g.delete_workout(workout_id)
    log.info("Training gelöscht: %s (%s)", w.get("workoutName"), workout_id)
    return {"geloescht": w.get("workoutName"), "workout_id": workout_id}


def create(day: str, name: str, schritte: list, beschreibung: str | None = None,
           vorschau: bool = False, ersetzen: bool = False) -> dict:
    payload = _strip_none(build(name, schritte, beschreibung))
    preview = {
        "name": payload["workoutName"],
        "tag": day,
        "geschaetzt_min": round((payload.get("estimatedDurationInSecs") or 0) / 60),
        "geschaetzt_km": round((payload.get("estimatedDistanceInMeters") or 0) / 1000, 1),
        "schritte": describe_steps(payload["workoutSegments"][0]["workoutSteps"]),
    }
    if vorschau:
        _check_day(day)
        return {"status": "vorschau", **preview, "garmin_json": payload}

    day = _check_day(day)
    with _client_lock:
        g = _garmin()
        # Doppelte vermeiden: gleicher Name am selben Tag
        existing = [s for s in (g.get_scheduled_workouts_by_period(day, day) or [])
                    if s.get("calendarDate") == day
                    and (s.get("workout") or {}).get("workoutName") == payload["workoutName"]]
        replaced = []
        if existing:
            if not ersetzen:
                return {"status": "schon_vorhanden", "hinweis": "Gleiches Training an diesem Tag schon geplant; "
                        "ersetzen=True überschreibt es.",
                        "workout_id": existing[0]["workout"].get("workoutId")}
            for s in existing:
                replaced.append(_delete_coach_workout(g, s["workout"]["workoutId"]))

        res = g.upload_workout(payload)
        wid = (res or {}).get("workoutId")
        if not wid:
            raise RuntimeError(f"Garmin hat keine workoutId zurückgegeben: {str(res)[:300]}")
        try:
            sched = g.schedule_workout(wid, day)
        except Exception:
            # Ohne Termin nutzt das Training nichts; Vorlage wieder entfernen
            try:
                g.delete_workout(wid)
            except Exception:
                log.warning("Vorlage %s nach Fehler beim Einplanen nicht gelöscht", wid)
            raise
    log.info("Training angelegt und eingeplant: %s am %s (%s)", payload["workoutName"], day, wid)
    return {"status": "eingeplant", **preview, "workout_id": wid,
            "schedule_id": (sched or {}).get("workoutScheduleId"),
            "ersetzt": replaced or None,
            "hinweis": "Die Uhr lädt das Training beim nächsten Sync mit Garmin Connect."}


def delete(workout_id: int) -> dict:
    with _client_lock:
        return _delete_coach_workout(_garmin(), int(workout_id))
