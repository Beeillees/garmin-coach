"""Sensoren aus der Garmin-Coach-Datenbank.

Das Add-on liefert unter /ha/state eine Liste von Sensoren (key, name, state, unit, device_class,
state_class, icon, attributes). Hier wird daraus je eine Entität sensor.garmin_coach_<key>.
Neue Sensoren, die das Add-on später liefert, kommen ohne HA-Neustart dazu.
Aktualisiert wird nach jedem Sync (Ereignis garmin_coach_updated) und alle POLL_MINUTES Minuten.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from aiohttp import ClientError, ClientTimeout

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util import dt as dt_util

from .const import DOMAIN, POLL_MINUTES, STATE_PATH, UPDATE_EVENT

_LOGGER = logging.getLogger(__name__)

TIMEOUT = ClientTimeout(total=30)


class CoachCoordinator(DataUpdateCoordinator[dict]):
    def __init__(self, hass: HomeAssistant, url: str) -> None:
        super().__init__(hass, _LOGGER, name="Garmin Coach", update_interval=timedelta(minutes=POLL_MINUTES))
        self.url = url
        self.session = async_get_clientsession(hass)

    async def _async_update_data(self) -> dict:
        try:
            async with self.session.get(self.url, timeout=TIMEOUT) as resp:
                data = await resp.json(content_type=None)
                if resp.status != 200:
                    raise UpdateFailed(f"Add-on meldet {resp.status}: {data.get('fehler') if isinstance(data, dict) else data}")
        except (ClientError, TimeoutError, ValueError) as err:
            raise UpdateFailed(f"Garmin-Coach-Add-on nicht erreichbar: {err}") from err
        sensors = {s["key"]: s for s in data.get("sensoren", []) if isinstance(s, dict) and s.get("key")}
        return {"sensoren": sensors, "version": data.get("version"), "erzeugt": data.get("erzeugt")}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    target = hass.data[DOMAIN][entry.entry_id]["target"]
    base = target.rsplit("/", 1)[0] if target.endswith("/mcp") else target.rstrip("/")
    coordinator = CoachCoordinator(hass, base + STATE_PATH)
    # Kein first_refresh: Ist das Add-on beim HA-Start noch nicht da, soll die MCP-Weiterleitung
    # trotzdem laufen. Die Sensoren erscheinen dann mit dem ersten erfolgreichen Abruf.
    await coordinator.async_refresh()

    known: set[str] = set()

    @callback
    def add_new() -> None:
        new = [CoachSensor(coordinator, entry, key) for key in (coordinator.data or {}).get("sensoren", {})
               if key not in known]
        if new:
            known.update(s.key for s in new)
            async_add_entities(new)

    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))

    async def on_update(event: Event) -> None:
        await coordinator.async_request_refresh()

    entry.async_on_unload(hass.bus.async_listen(UPDATE_EVENT, on_update))


def _device_class(value):
    try:
        return SensorDeviceClass(value) if value else None
    except ValueError:
        return None


def _state_class(value):
    try:
        return SensorStateClass(value) if value else None
    except ValueError:
        return None


class CoachSensor(CoordinatorEntity[CoachCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: CoachCoordinator, entry: ConfigEntry, key: str) -> None:
        super().__init__(coordinator)
        self.key = key
        spec = coordinator.data["sensoren"][key]
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self.entity_id = f"sensor.garmin_coach_{key}"
        self._attr_name = spec.get("name") or key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)}, name="Garmin Coach", manufacturer="Garmin Coach Add-on",
            model="Lokale Garmin-Datenbank", entry_type=DeviceEntryType.SERVICE,
            sw_version=coordinator.data.get("version"),
        )

    @property
    def _spec(self) -> dict | None:
        return ((self.coordinator.data or {}).get("sensoren") or {}).get(self.key)

    @property
    def available(self) -> bool:
        return super().available and self._spec is not None

    @property
    def native_unit_of_measurement(self):
        return (self._spec or {}).get("unit")

    @property
    def device_class(self):
        return _device_class((self._spec or {}).get("device_class"))

    @property
    def state_class(self):
        spec = self._spec or {}
        # Nur Zahlen können eine state_class haben, sonst meckert HA
        return _state_class(spec.get("state_class")) if isinstance(spec.get("state"), (int, float)) else None

    @property
    def icon(self):
        return (self._spec or {}).get("icon")

    @property
    def native_value(self):
        spec = self._spec or {}
        value = spec.get("state")
        if value is None:
            return None
        dc = spec.get("device_class")
        if dc == "timestamp":
            return dt_util.parse_datetime(str(value))
        if dc == "date":
            try:
                return date.fromisoformat(str(value)[:10])
            except ValueError:
                return None
        if isinstance(value, (dict, list)):
            return None
        if isinstance(value, str) and len(value) > 255:
            return value[:252] + "..."
        return value

    @property
    def extra_state_attributes(self):
        return (self._spec or {}).get("attributes") or None
