"""Richtet die Webhook-Weiterleitung in Home Assistant ein.

1. Kopiert die kleine Integration nach custom_components/garmin_coach_proxy
2. Hinterlegt dort Webhook-ID und interne Adresse des MCP-Servers
3. Legt den Integrationseintrag über die HA-API an (nach einem HA-Neustart)
4. Schreibt die fertige Connector-URL ins Protokoll
"""

import filecmp
import json
import logging
import os
import secrets
import shutil
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path

from mcp_server import PATH, PORT

log = logging.getLogger("garmin_coach.ha")

DOMAIN = "garmin_coach_proxy"
SOURCE_DIR = Path("/app/ha_integration") / DOMAIN
WEBHOOK_ID_FILE = Path("/data/webhook_id")
CORE_API = "http://supervisor/core/api"
RETRY_S = 120


def _ha_config_dir() -> Path | None:
    for p in (Path("/homeassistant"), Path("/config")):
        if (p / "configuration.yaml").exists():
            return p
    return None


def _api(method: str, path: str, body: dict | None = None):
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        raise RuntimeError("SUPERVISOR_TOKEN fehlt (homeassistant_api nicht aktiv?)")
    req = urllib.request.Request(
        CORE_API + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw.decode(errors="replace")


def webhook_id() -> str:
    if WEBHOOK_ID_FILE.exists():
        wid = WEBHOOK_ID_FILE.read_text().strip()
        if wid:
            return wid
    wid = f"garmin_coach_{secrets.token_hex(16)}"
    WEBHOOK_ID_FILE.write_text(wid)
    return wid


def _target() -> str:
    host = os.environ.get("HOSTNAME") or socket.gethostname()
    return f"http://{host}:{PORT}{PATH}"


def _tree_differs(src: Path, dest: Path) -> bool:
    """True, wenn eine Datei aus src in dest fehlt oder anders ist (rekursiv)."""
    for f in src.rglob("*"):
        if f.is_file():
            other = dest / f.relative_to(src)
            if not other.exists() or not filecmp.cmp(f, other, shallow=False):
                return True
    return False


def install_integration(cfg: Path, wid: str) -> tuple[bool, bool]:
    """Kopiert die Integration und schreibt die Einstellungen.

    Rückgabe: (Code geändert -> HA-Neustart nötig, Einstellungen geändert -> Neuladen nötig)
    """
    dest = cfg / "custom_components" / DOMAIN
    code_changed = _tree_differs(SOURCE_DIR, dest)
    if code_changed:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(SOURCE_DIR, dest, dirs_exist_ok=True)
        log.info("Integration nach %s kopiert", dest)

    settings = {"webhook_id": wid, "target": _target()}
    settings_file = dest / "settings.json"
    old = None
    if settings_file.exists():
        try:
            old = json.loads(settings_file.read_text())
        except ValueError:
            pass
    settings_changed = old != settings
    if settings_changed:
        settings_file.write_text(json.dumps(settings, indent=2))
    return code_changed, settings_changed


def _entries() -> list:
    status, data = _api("GET", f"/config/config_entries/entry?domain={DOMAIN}")
    if status != 200 or not isinstance(data, list):
        raise RuntimeError(f"Integrationseinträge nicht lesbar ({status}): {data}")
    return [e for e in data if e.get("domain") == DOMAIN]


def ensure_entry(settings_changed: bool) -> bool:
    """True, wenn der Integrationseintrag existiert und aktiv ist."""
    entries = _entries()
    if entries:
        entry = entries[0]
        if settings_changed or entry.get("state") not in ("loaded",):
            status, _ = _api("POST", f"/config/config_entries/entry/{entry['entry_id']}/reload")
            log.info("Integration neu geladen (%s)", status)
        return True
    status, data = _api("POST", "/config/config_entries/flow", {"handler": DOMAIN})
    if status == 200 and isinstance(data, dict) and data.get("type") == "create_entry":
        log.info("Integration 'Garmin Coach MCP' in Home Assistant eingerichtet")
        return True
    return False  # Integration ist HA noch nicht bekannt -> Neustart nötig


def external_url(wid: str) -> str:
    try:
        status, cfg = _api("GET", "/config")
        base = (cfg or {}).get("external_url") if status == 200 else None
    except Exception:
        base = None
    path = f"/api/webhook/{wid}"
    return f"{base.rstrip('/')}{path}" if base else f"https://<deine-ha-adresse>{path}"


def run() -> None:
    """Läuft im Hintergrund, bis die Weiterleitung steht."""
    cfg = _ha_config_dir()
    if cfg is None:
        log.error("HA-Konfigurationsordner nicht gefunden, Weiterleitung kann nicht eingerichtet werden")
        return
    wid = webhook_id()
    try:
        code_changed, settings_changed = install_integration(cfg, wid)
    except Exception as e:
        log.error("Integration konnte nicht installiert werden: %s", e)
        return

    url = external_url(wid)
    warned = False
    while True:
        try:
            if ensure_entry(settings_changed):
                if code_changed and not warned:
                    log.warning("Die Integration wurde aktualisiert. Für die neue Version bitte "
                                "Home Assistant bei Gelegenheit neu starten.")
                log.info("=" * 70)
                log.info("MCP-Weiterleitung aktiv. URL für den Claude-Connector:")
                log.info("  %s", url)
                log.info("Die URL ist geheim: Wer sie kennt, kann deine Trainingsdaten lesen.")
                log.info("=" * 70)
                return
            if not warned:
                log.warning("=" * 70)
                log.warning("Bitte Home Assistant EINMAL KOMPLETT NEU STARTEN")
                log.warning("(Einstellungen > System > oben rechts > Neu starten).")
                log.warning("Danach richtet sich die Weiterleitung automatisch ein.")
                log.warning("=" * 70)
                warned = True
        except Exception as e:
            log.warning("Einrichtung der Weiterleitung: %s (neuer Versuch in %d s)", e, RETRY_S)
        time.sleep(RETRY_S)
