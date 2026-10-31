"""Zeitplan des Sammlers und manuelle Sync-Anfragen über MCP.

- Voller Sync zu festen Uhrzeiten (Option `sync_times`, lokale Zeit).
- Dazwischen im Zeitfenster `activity_check_window` alle `activity_check_minutes` eine
  Schnellprüfung auf neue Aktivitäten (ein einziger Abruf bei Garmin).
- Das MCP-Werkzeug `sync_now` kann jederzeit einen Lauf anstoßen und auf das Ergebnis warten.
"""

import threading
from datetime import datetime, time as dtime, timedelta

DEFAULT_SYNC_TIMES = ["05:15", "19:45"]
DEFAULT_CHECK_WINDOW = "06:00-22:00"
DEFAULT_CHECK_MINUTES = 20


def _parse_hhmm(s: str) -> dtime:
    h, m = str(s).strip().split(":")
    return dtime(int(h), int(m))


class Schedule:
    def __init__(self, opts: dict):
        times = opts.get("sync_times") or DEFAULT_SYNC_TIMES
        self.sync_times = sorted({_parse_hhmm(t) for t in times})
        window = str(opts.get("activity_check_window") or DEFAULT_CHECK_WINDOW)
        a, b = window.split("-")
        self.window = (_parse_hhmm(a), _parse_hhmm(b))
        self.check_minutes = int(opts.get("activity_check_minutes") or DEFAULT_CHECK_MINUTES)

    def describe(self) -> str:
        times = ", ".join(t.strftime("%H:%M") for t in self.sync_times)
        if self.check_minutes <= 0:
            return f"voller Sync um {times}; keine Schnellprüfung"
        return (f"voller Sync um {times}; Schnellprüfung alle {self.check_minutes} min "
                f"von {self.window[0]:%H:%M} bis {self.window[1]:%H:%M}")

    def next_full(self, now: datetime) -> datetime:
        for day in (0, 1):
            d = (now + timedelta(days=day)).date()
            for t in self.sync_times:
                cand = datetime.combine(d, t)
                if cand > now:
                    return cand
        return datetime.combine((now + timedelta(days=1)).date(), self.sync_times[0])

    def next_quick(self, now: datetime):
        if self.check_minutes <= 0:
            return None
        start, end = self.window
        step = timedelta(minutes=self.check_minutes)
        for day in (0, 1):
            d = (now + timedelta(days=day)).date()
            cand = datetime.combine(d, start)
            stop = datetime.combine(d, end)
            while cand <= stop:
                if cand > now:
                    return cand
                cand += step
        return None

    def next_run(self, now: datetime) -> tuple[datetime, str]:
        """(Zeitpunkt, 'voll' | 'schnell'). Fällt beides zusammen, gewinnt der volle Sync."""
        full = self.next_full(now)
        quick = self.next_quick(now)
        if quick is not None and quick < full:
            return quick, "schnell"
        return full, "voll"


# --- Manuelle Anfragen (MCP-Thread -> Sync-Thread) ------------------------------

class _Request:
    def __init__(self, kind: str):
        self.kind = kind
        self.done = threading.Event()
        self.result = None


_lock = threading.Lock()
_wake = threading.Event()
_pending = None  # _Request
_busy = threading.Event()  # gesetzt, solange der Sync-Thread arbeitet
worker_ready = threading.Event()  # gesetzt, sobald der Sync-Thread angemeldet ist


def request(kind: str, timeout_s: float):
    """Sync anstoßen und bis timeout_s auf das Ergebnis warten. Gibt das Ergebnis oder None zurück."""
    global _pending
    with _lock:
        if _pending is None:
            _pending = _Request(kind)
        elif kind == "voll":
            _pending.kind = "voll"  # eine schon wartende Schnellprüfung wird zum vollen Sync
        req = _pending
    _wake.set()
    req.done.wait(timeout_s)
    return req.result if req.done.is_set() else None


def wait_for_next(timeout_s: float):
    """Im Sync-Thread: schlafen bis zur nächsten geplanten Zeit oder bis eine Anfrage kommt.

    Gibt die Anfrage zurück (oder None, wenn die Zeit abgelaufen ist)."""
    global _pending
    _wake.wait(max(0.0, timeout_s))
    with _lock:
        _wake.clear()
        req, _pending = _pending, None
    return req


def finish(req, result) -> None:
    if req is not None:
        req.result = result
        req.done.set()


def is_busy() -> bool:
    return _busy.is_set()


def set_busy(on: bool) -> None:
    (_busy.set if on else _busy.clear)()
