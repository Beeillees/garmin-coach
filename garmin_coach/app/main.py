"""Einstiegspunkt des Add-ons: Sammler im Hintergrund, MCP-Server im Vordergrund."""

import asyncio
import json
import logging
import sys
import threading
import time
from pathlib import Path

from garminconnect import Garmin

import fit_store
from db import Store
from sync import run_sync

OPTIONS_FILE = Path("/data/options.json")
TOKEN_DIR = Path("/data/garmin_tokens")
DATA_DIR = Path("/share/garmin-coach")
DB_FILE = DATA_DIR / "garmin.db"
MFA_FILE = DATA_DIR / "mfa_code.txt"
BACKFILL_MARKER = Path("/data/backfill_done")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("garmin_coach")


def load_options() -> dict:
    return json.loads(OPTIONS_FILE.read_text())


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
    if backfill > 0 and not BACKFILL_MARKER.exists():
        log.info("Starte Backfill über %d Tage, das kann eine Weile dauern ...", backfill)
        run_sync(g, store, DATA_DIR, backfill)
        BACKFILL_MARKER.write_text(str(backfill))
        fit_store.process_pending(store, DATA_DIR)

    interval = int(opts.get("sync_interval_hours") or 6) * 3600
    days_back = int(opts.get("days_back") or 30)
    while True:
        try:
            run_sync(g, store, DATA_DIR, days_back)
        except Exception as e:
            log.error("Sync fehlgeschlagen: %s", e)
            try:
                g = login(opts)  # Token abgelaufen o. Ä.: neu anmelden
            except Exception as e2:
                log.error("Erneuter Login fehlgeschlagen: %s", e2)
        try:
            fit_store.process_pending(store, DATA_DIR)
        except Exception as e:
            log.error("FIT-Auswertung fehlgeschlagen: %s", e)
        time.sleep(interval)


def _thread(name: str, target, *args) -> None:
    def runner():
        try:
            target(*args)
        except Exception:
            log.exception("%s ist abgestürzt", name)
    threading.Thread(target=runner, name=name, daemon=True).start()


def main() -> None:
    opts = load_options()
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Datenbank-Schema anlegen, bevor der MCP-Server darauf zugreift
    store = Store(DB_FILE)
    fit_store.init(store)
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
