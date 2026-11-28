"""Stockwerke der StepMill-App in Garmin Connect nachtragen.

Die Connect-IQ-App „StepMill“ (Brunos eigene, fēnix 8) zählt auf der StepMill die Stufen über die
Uhrschritte und schreibt Stockwerke als Entwicklerfeld `stockwerke` in die FIT-Datei. Garmins eigenes
Feld „Stockwerke“ bleibt dabei 0, weil eine Connect-IQ-App nur eigene Felder schreiben darf und das
Barometer auf der StepMill nichts misst. Bisher hat Bruno die Stockwerke von Hand in Connect
eingetragen; dieses Modul macht das nach jeder FIT-Auswertung automatisch.

Sicherheit:
- Nur Einheiten der StepMill-App (FIT-Session mit `stockwerke` und `stufenhoehe_cm`).
- Nur wenn Connect dort 0 oder nichts stehen hat, von Hand eingetragene Werte bleiben unberührt.
- Automatisch erst ab AUTO_AB: ältere Einheiten wurden mit doppelt gezählten Schritten aufgezeichnet
  (App-Version vor v07), die korrigiert man gezielt mit `set_floors(activity_id, stockwerke)`.
- Jede Änderung landet in `stepmill_floors` (vorher/nachher), nichts wird zweimal versucht.
"""

import json
import logging

from db import now_iso

log = logging.getLogger("garmin_coach.stepmill")

# v07 (richtige Kalibrierung: 2 Uhrschritte pro Stufe, 15,5 Stufen pro Stockwerk) ab 28.11.2026 13:05
AUTO_AB = "2026-11-28T13:00"
MAX_FLOORS = 2000

SCHEMA = """
CREATE TABLE IF NOT EXISTS stepmill_floors (
    activity_id INTEGER PRIMARY KEY,
    floors INTEGER,
    before INTEGER,
    after INTEGER,
    status TEXT NOT NULL,
    quelle TEXT NOT NULL,
    error TEXT,
    at TEXT NOT NULL
);
"""


def init(store) -> None:
    store.conn.executescript(SCHEMA)
    store.conn.commit()


def _floors_of(detail: dict):
    """Stockwerke aus der Aktivitäts-Detailansicht (summaryDTO, zur Sicherheit auch oben)."""
    s = (detail or {}).get("summaryDTO") or {}
    v = s.get("floorsClimbed", (detail or {}).get("floorsClimbed"))
    return int(v) if isinstance(v, (int, float)) else None


def write_floors(g, activity_id: int, floors: int, overwrite: bool = False) -> dict:
    """Trägt `floors` bei Garmin Connect ein und prüft das Ergebnis mit einem zweiten Abruf.

    Schickt das vollständige summaryDTO mit (nur floorsClimbed geändert), damit Connect keine
    anderen Werte zurücksetzt."""
    if not isinstance(floors, int) or not 0 < floors <= MAX_FLOORS:
        raise ValueError(f"Stockwerke {floors!r} unplausibel (1–{MAX_FLOORS})")
    detail = g.get_activity(activity_id)
    before = _floors_of(detail)
    if before and not overwrite:
        return {"status": "uebersprungen", "grund": "Connect hat schon Stockwerke", "vorher": before}
    summary = dict(detail.get("summaryDTO") or {})
    summary["floorsClimbed"] = floors
    g.update_activity(activity_id, {"summaryDTO": summary})
    after = _floors_of(g.get_activity(activity_id))
    ok = after == floors
    return {"status": "ok" if ok else "nicht_uebernommen", "vorher": before, "nachher": after,
            "gesendet": floors}


def _save(store, activity_id: int, floors, res: dict, quelle: str, error: str | None = None) -> None:
    store.conn.execute(
        "INSERT OR REPLACE INTO stepmill_floors(activity_id, floors, before, after, status, quelle, error, at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (activity_id, floors, res.get("vorher"), res.get("nachher"), res.get("status", "fehler"),
         quelle, error, now_iso()),
    )
    if res.get("status") == "ok":
        # lokale Kopie gleich mitziehen, damit treppen_hm & Co. ohne neuen Sync stimmen
        store.conn.execute(
            "UPDATE activities SET data = json_set(data, '$.floorsClimbed', ?) WHERE activity_id = ?",
            (floors, activity_id),
        )
    store.conn.commit()


def pending(store) -> list[tuple[int, int]]:
    """StepMill-Einheiten ab AUTO_AB, deren Stockwerke in Connect noch fehlen."""
    rows = store.conn.execute(
        """
        SELECT s.activity_id, json_extract(s.data, '$.stockwerke') AS stw
        FROM fit_sessions s
        JOIN activities a ON a.activity_id = s.activity_id
        LEFT JOIN stepmill_floors f ON f.activity_id = s.activity_id
        WHERE json_extract(s.data, '$.stockwerke') IS NOT NULL
          AND json_extract(s.data, '$.stufenhoehe_cm') IS NOT NULL
          AND COALESCE(json_extract(a.data, '$.floorsClimbed'), 0) = 0
          AND a.start_local >= ?
          AND f.activity_id IS NULL
        ORDER BY a.start_local
        """,
        (AUTO_AB.replace("T", " "),),
    ).fetchall()
    return [(int(aid), int(round(stw))) for aid, stw in rows if stw and stw >= 1]


def sync_floors(g, store) -> int:
    """Nach der FIT-Auswertung: fehlende StepMill-Stockwerke nachtragen. Rückgabe: Anzahl ok."""
    done = 0
    for aid, floors in pending(store):
        try:
            res = write_floors(g, aid, floors)
            _save(store, aid, floors, res, "auto")
            log.info("StepMill %s: %s Stockwerke -> %s (vorher %s, nachher %s)",
                     aid, floors, res["status"], res.get("vorher"), res.get("nachher"))
            done += res["status"] == "ok"
        except Exception as e:
            _save(store, aid, floors, {"status": "fehler"}, "auto", repr(e)[:500])
            log.warning("StepMill %s: Stockwerke nachtragen fehlgeschlagen: %s", aid, e)
    return done


def set_floors_manual(g, store, activity_id: int, floors: int | None = None) -> dict:
    """Für das MCP-Werkzeug: eine Einheit gezielt setzen (auch überschreiben).
    Ohne `floors` gilt der Wert aus der FIT-Datei."""
    if floors is None:
        row = store.conn.execute(
            "SELECT json_extract(data, '$.stockwerke') FROM fit_sessions WHERE activity_id = ?",
            (activity_id,),
        ).fetchone()
        if not row or row[0] is None:
            raise ValueError("Keine StepMill-Stockwerke in der FIT-Datei, bitte `stockwerke` angeben")
        floors = int(round(row[0]))
    floors = int(floors)
    try:
        res = write_floors(g, activity_id, floors, overwrite=True)
    except Exception as e:
        _save(store, activity_id, floors, {"status": "fehler"}, "manuell", repr(e)[:500])
        raise
    _save(store, activity_id, floors, res, "manuell")
    return {"activity_id": activity_id, **res}
