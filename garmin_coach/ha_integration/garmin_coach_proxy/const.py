"""Konstanten der Garmin-Coach-Weiterleitung."""

from pathlib import Path

DOMAIN = "garmin_coach_proxy"

# Das Add-on schreibt Webhook-ID und Ziel-Adresse in diese Datei.
SETTINGS_FILE = Path(__file__).parent / "settings.json"

# Header, die zwischen Claude und dem MCP-Server durchgereicht werden. Zusätzlich gehen
# alle Header mit dem Präfix "mcp-" durch (mcp-session-id, mcp-protocol-version,
# mcp-method, mcp-name, Mcp-Param-*), damit auch künftige Protokollversionen funktionieren.
FORWARD_REQUEST_HEADERS = ("content-type", "accept", "last-event-id")
FORWARD_RESPONSE_HEADERS = ("content-type",)
FORWARD_PREFIX = "mcp-"

# Sensoren: Pfad am Add-on (neben /mcp), Ereignis nach jedem Sync, Abruf-Intervall als Rückfall
STATE_PATH = "/ha/state"
UPDATE_EVENT = "garmin_coach_updated"
POLL_MINUTES = 10


def pick_headers(headers, names) -> dict:
    return {k: v for k, v in headers.items()
            if k.lower() in names or k.lower().startswith(FORWARD_PREFIX)}
