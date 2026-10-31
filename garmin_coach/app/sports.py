"""Einheitliche Sportart-Gruppen.

Garmin nutzt zwei Namenswelten: den Aktivitätstyp aus Garmin Connect (`type_key`,
z. B. treadmill_running, ultra_run, breathwork) und die Sportart in der FIT-Datei
(z. B. running, generic). Für Umfänge und Auswertungen fassen wir beides zu einer
festen Gruppe zusammen.
"""

GROUPS = {
    "laufen": {
        "running", "treadmill_running", "ultra_run", "trail_running", "track_running",
        "street_running", "indoor_running", "virtual_run", "obstacle_run",
    },
    "rad": {
        "cycling", "indoor_cycling", "road_biking", "mountain_biking", "gravel_cycling",
        "virtual_ride", "bmx", "cyclocross", "e_bike_fitness", "e_bike_mountain", "track_cycling",
        "recumbent_cycling", "hand_cycling", "indoor_hand_cycling",
    },
    "schwimmen": {"lap_swimming", "open_water_swimming", "swimming"},
    "kraft_hiit": {
        "hiit", "strength_training", "indoor_rowing", "rowing", "cross_country_indoor_skiing",
        "floor_climbing", "indoor_climbing", "bouldering", "cardio_training", "fitness_equipment",
        "elliptical", "stair_climbing", "jump_rope", "pilates", "indoor_cardio",
    },
    "gehen": {"walking", "hiking", "casual_walking", "speed_walking", "mountaineering"},
    "kein_sport": {"breathwork", "meditation", "yoga_breathing"},
}

# Gruppen, die nicht in Trainingsumfänge und -last gehören
NO_TRAINING = {"kein_sport"}

_LOOKUP = {key: group for group, keys in GROUPS.items() for key in keys}


def group(type_key: str | None, fit_sport: str | None = None) -> str:
    """Gruppe zu Garmin-Typ bzw. FIT-Sportart; Garmin-Typ hat Vorrang."""
    for key in (type_key, fit_sport):
        if key and key in _LOOKUP:
            return _LOOKUP[key]
    for key in (type_key, fit_sport):  # unbekannte Varianten wie "trail_ultra_run"
        if not key:
            continue
        if "run" in key:
            return "laufen"
        if "cycl" in key or "bik" in key or "ride" in key:
            return "rad"
        if "swim" in key:
            return "schwimmen"
    return "sonstiges"


def matches(filter_value: str, type_key: str | None, fit_sport: str | None) -> bool:
    """Filter akzeptiert eine Gruppe (z. B. 'laufen') oder einen Typ (z. B. 'treadmill_running')."""
    f = (filter_value or "").strip().lower()
    if not f:
        return True
    if f in GROUPS or f == "sonstiges":
        return group(type_key, fit_sport) == f
    return f in (type_key, fit_sport)
