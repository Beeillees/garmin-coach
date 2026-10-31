"""Garmin Coach MCP: leitet einen Home-Assistant-Webhook an den MCP-Server des Add-ons weiter.

So ist der MCP-Server über die bestehende externe HA-Adresse erreichbar
(https://<ha>/api/webhook/<id>), ohne zusätzlichen Port oder Hostnamen.
"""

from __future__ import annotations

import json
import logging

from aiohttp import ClientError, ClientTimeout, web

from homeassistant.components import webhook
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    DOMAIN,
    FORWARD_REQUEST_HEADERS,
    FORWARD_RESPONSE_HEADERS,
    SETTINGS_FILE,
    pick_headers,
)

_LOGGER = logging.getLogger(__name__)

TIMEOUT = ClientTimeout(total=120)


def _load_settings() -> dict | None:
    try:
        data = json.loads(SETTINGS_FILE.read_text())
    except (OSError, ValueError):
        return None
    if not data.get("webhook_id") or not data.get("target"):
        return None
    return data


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    settings = await hass.async_add_executor_job(_load_settings)
    if not settings:
        raise ConfigEntryNotReady("Einstellungen vom Garmin-Coach-Add-on fehlen noch")

    target = settings["target"]
    session = async_get_clientsession(hass)

    async def handle(hass: HomeAssistant, webhook_id: str, request: web.Request) -> web.Response:
        body = await request.read()
        headers = pick_headers(request.headers, FORWARD_REQUEST_HEADERS)
        try:
            async with session.post(target, data=body, headers=headers, timeout=TIMEOUT) as resp:
                content = await resp.read()
                out_headers = pick_headers(resp.headers, FORWARD_RESPONSE_HEADERS)
                return web.Response(status=resp.status, body=content, headers=out_headers)
        except (ClientError, TimeoutError) as err:
            _LOGGER.warning("Garmin-Coach-Add-on nicht erreichbar (%s): %s", target, err)
            return web.json_response(
                {"jsonrpc": "2.0", "id": None,
                 "error": {"code": -32000, "message": "Garmin-Coach-Add-on nicht erreichbar"}},
                status=502,
            )

    # Nur POST: GET würde beim MCP-Server einen dauerhaften Ereignisstrom öffnen,
    # den wir im stateless-Betrieb nicht brauchen. HA antwortet auf GET mit 405.
    webhook.async_register(
        hass, DOMAIN, "Garmin Coach MCP", settings["webhook_id"], handle,
        local_only=False, allowed_methods=["POST"],
    )
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = settings["webhook_id"]
    _LOGGER.info("Garmin Coach MCP aktiv, Ziel %s", target)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    webhook_id = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if webhook_id:
        webhook.async_unregister(hass, webhook_id)
    return True
