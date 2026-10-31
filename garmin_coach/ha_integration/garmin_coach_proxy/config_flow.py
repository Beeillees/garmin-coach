"""Einrichtung ohne Eingaben: alles Nötige liefert das Add-on."""

from __future__ import annotations

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from . import _load_settings
from .const import DOMAIN


class GarminCoachProxyFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None) -> ConfigFlowResult:
        settings = await self.hass.async_add_executor_job(_load_settings)
        if not settings:
            return self.async_abort(reason="addon_missing")
        return self.async_create_entry(title="Garmin Coach MCP", data={})
