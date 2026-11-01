"""Einstiegspunkt des Add-ons: Sammler im Hintergrund, MCP-Server im Vordergrund."""

import asyncio
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from garminconnect import Garmin

import fit_store
import scheduler
from db import Store
from sync import quick_check, run_sync, sync_history_v07

OPTIONS_FILE = Path("/data/options.json")
TOKEN_DIR = Path("/data/garmin_tokens")
DATA_DIR = Path("/share/garmin-coach")
DB_FILE = DATA_DIR / "garmin.db"
MFA_FILE = DATA_DIR / "mfa_code.txt"
BACKFILL_MARKER = Path("/data/backfill_done")


def _marker_days(path: Path) -> int:
    """Zahl der Tage, für die der Backfill schon lief (0 = noch nie)."""
    try:
        return int(path.read_text().strip() or 1)
    except FileNotFoundError:
        return 0
    except ValueError:
        return 1
# Einmaliges Nachladen der Historie von Laktatschwelle, Wettkampfprognosen usw.
# v2 ab 0.5.0: in 0.4.0 scheiterte der Abruf an Garmins 1-Jahres-Grenze, die Markierung wurde
# trotzdem gesetzt. Deshalb neuer Name, und gesetzt wird sie nur, wenn alle Abrufe geklappt haben.
HISTORY_MARKER = Path("/data/history_snapshots_v2_done")
# Mit 0.7.0 neu: Gewicht und Running Tolerance. Bestehende Installationen haben Backfill und
# Verläufe schon erledigt, deshalb eine eigene Markierung für das einmalige Nachladen.
HISTORY_V07_MARKER = Path("/data/history_v07_done")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("garmin_coach")


def load_options() -> dict:
    return json.loads(OPTIONS_FILE.read_text())


def apply_log_level(opts: dict) -> None:
    level = logging.DEBUG if str(opts.get("log_level") or "info").lower() == "debug" else logging.INFO
    logging.getLogger().setLevel(level)
    logging.getLogger("garmin_coach").setLevel(level)


def make_mfa_prompt(opts: dict):
    """Garmin kann beim ersten Login einen MFA-Code verlangen.

    Der Code kommt entweder aus der Add-on-Option `mfa_code` oder aus der Datei
    /share/garmin-coach/mfa_code.txt, auf die wir bis zu 10 Minuten warten.
    """

    def prompt() -> str:
        code = (opts.get("mfa_code") or "").strip()
        if code:
            return code
        log.warning(
            "Garmin verlangt einen MFA-Code. Schreib den Code aus der Mail/App in "
            "%s (nur die Ziffern). Ich warte bis zu 10 Minuten.",
            MFA_FILE,
        )
        for _ in range(120):
            if MFA_FILE.exists():
                code = MFA_FILE.read_text().strip()
                MFA_FILE.unlink(missing_ok=True)
                if code:
                    return code
            time.sleep(5)
        raise RuntimeError("Kein MFA-Code erhalten")

    return prompt


def login(opts: dict) -> Garmin:
    TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    g = Garmin(
        email=opts.get("email") or None,
        password=opts.get("password") or None,
        prompt_mfa=make_mfa_prompt(opts),
    )
    g.login(str(TOKEN_DIR))
    log.info("Bei Garmin Connect angemeldet")
    return g


def sync_worker(opts: dict) -> None:
    """Sammler und FIT-Auswertung, läuft im Hintergrund-Thread."""
    store = Store(DB_FILE)  # eigene Verbindung, SQLite-Verbindungen sind an ihren Thread gebunden
    fit_store.init(store)

    # Bereits vorhandene, noch nicht ausgewertete FIT-Dateien zuerst abarbeiten
    fit_store.process_pending(store, DATA_DIR)

    has_tokens = TOKEN_DIR.exists() and any(TOKEN_DIR.iterdir())
    if not has_tokens and not (opts.get("email") and opts.get("password")):
        log.error(
            "Keine Garmin-Zugangsdaten. Bitte im Reiter 'Konfiguration' E-Mail und "
            "Passwort eintragen, speichern und das Add-on neu starten. "
            "Der MCP-Server läuft trotzdem mit den vorhandenen Daten."
        )
        return

    while True:
        try:
            g = login(opts)
            break
        except Exception as e:
            log.error("Login bei Garmin fehlgeschlagen: %s (neuer Versuch in 30 min)", e)
            time.sleep(1800)

    # Einmaliges Nachladen alter Tageswerte und Aktivitäten
    backfill = int(opts.get("backfill_days") or 0)
    if backfill > 0 and _marker_days(BACKFILL_MARKER) < backfill:
        log.info("Starte Backfill über %d Tage, das kann eine Weile dauern ...", backfill)
        run_sync(g, store, DATA_DIR, backfill)
        BACKFILL_MARKER.write_text(str(backfill))
        fit_store.process_pending(store, DATA_DIR)

    days_back = int(opts.get("days_back") or 30)
    schedule = scheduler.Schedule(opts)
    log.info("Zeitplan: %s", schedule.describe())
    scheduler.worker_ready.set()

    def full_sync() -> dict:
        nonlocal g
        history_days = 0
        wanted = max(int(opts.get("backfill_days") or 0), 730)
        if _marker_days(HISTORY_MARKER) < wanted:
            history_days = wanted
            log.info("Lade einmalig die Historie von Laktatschwelle, Prognosen usw. (%d Tage)", history_days)
        try:
            wanted_v07 = max(int(opts.get("backfill_days") or 0), days_back, 730)
            if _marker_days(HISTORY_V07_MARKER) < wanted_v07:
                log.info("Lade einmalig Gewicht und Running Tolerance nach (%d Tage)", wanted_v07)
                if sync_history_v07(g, store, wanted_v07):
                    HISTORY_V07_MARKER.write_text(str(wanted_v07))
            result = run_sync(g, store, DATA_DIR, days_back, history_days)
            if result.get("verlauf_ok"):
                HISTORY_MARKER.write_text(str(history_days))
            elif history_days:
                log.warning("Verläufe nicht vollständig geladen, nächster voller Sync versucht es erneut")
            return result
        except Exception as e:
            log.error("Sync fehlgeschlagen: %s", e)
            try:
                g = login(opts)  # Token abgelaufen o. Ä.: neu anmelden
            except Exception as e2:
                log.error("Erneuter Login fehlgeschlagen: %s", e2)
            return {"fehler": str(e)}

    def quick() -> dict:
        nonlocal g
        try:
            return quick_check(g, store, DATA_DIR)
        except Exception as e:
            log.warning("Schnellprüfung fehlgeschlagen: %s", e)
            try:
                g = login(opts)
            except Exception as e2:
                log.error("Erneuter Login fehlgeschlagen: %s", e2)
            return {"fehler": str(e)}

    def run(kind: str) -> dict:
        scheduler.set_busy(True)
        try:
            result = full_sync() if kind == "voll" else quick()
            if "fehler" not in result and (kind == "voll" or result.get("neue_fit")):
                try:
                    fit_store.process_pending(store, DATA_DIR)
                except Exception as e:
                    log.error("FIT-Auswertung fehlgeschlagen: %s", e)
            return result
        finally:
            scheduler.set_busy(False)

    run("voll")  # beim Start einmal alles holen
    while True:
        due, kind = schedule.next_run(datetime.now())
        log.debug("Nächster Lauf (%s) um %s", kind, due.strftime("%H:%M"))
        req = scheduler.wait_for_next((due - datetime.now()).total_seconds())
        if req is not None:
            log.info("Sync auf Anfrage (%s)", req.kind)
            scheduler.finish(req, run(req.kind))
        elif datetime.now() >= due:
            run(kind)


def _thread(name: str, target, *args) -> None:
    def runner():
        try:
            target(*args)
        except Exception:
            log.exception("%s ist abgestürzt", name)
    threading.Thread(target=runner, name=name, daemon=True).start()


def main() -> None:
    # Lokale Zeit für den Zeitplan; Home Assistant setzt TZ normalerweise selbst.
    os.environ.setdefault("TZ", "Europe/Berlin")
    time.tzset()
    opts = load_options()
    apply_log_level(opts)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Datenbank-Schema anlegen, bevor der MCP-Server darauf zugreift
    store = Store(DB_FILE)
    fit_store.init(store)
    n = store.close_orphaned_syncs()
    if n:
        log.info("%d unterbrochene Sync-Läufe als 'abgebrochen' markiert", n)
    store.conn.close()

    _thread("sync", sync_worker, opts)

    if not opts.get("mcp_enabled", True):
        log.info("MCP-Server ist in den Einstellungen deaktiviert")
        while True:
            time.sleep(3600)

    import ha_setup
    import mcp_server

    _thread("ha_setup", ha_setup.run)
    asyncio.run(mcp_server.serve())


if __name__ == "__main__":
    main()
