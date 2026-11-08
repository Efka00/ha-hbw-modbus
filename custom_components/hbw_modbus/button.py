"""Knöpfe am Bus-Gerät: nach neuen Modulen suchen."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .discovery import HbwBus


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    bus: HbwBus = entry.runtime_data
    async_add_entities([HbwSearchButton(bus, "search_new", False), HbwSearchButton(bus, "scan_bus", True)])


class HbwSearchButton(ButtonEntity):
    """search_new: nur ID 247 fragen; scan_bus: alle nicht eingerichteten IDs."""

    _attr_has_entity_name = True

    def __init__(self, bus: HbwBus, key: str, full: bool) -> None:
        self._bus = bus
        self._full = full
        self._attr_translation_key = key
        self._attr_unique_id = f"{bus.entry.unique_id}_{key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, bus.entry.unique_id)})

    async def async_press(self) -> None:
        self._bus.start(self._full)
