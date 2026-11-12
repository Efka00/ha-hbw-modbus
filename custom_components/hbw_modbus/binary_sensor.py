"""Verbindung zum Modul: am Modulgerät sichtbar, auch wenn das Modul fehlt."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HbwConfigEntry
from .coordinator import HbwCoordinator
from .entity import HbwEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: HbwConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([HbwConnection(entry.runtime_data.coordinator)])


class HbwConnection(HbwEntity, BinarySensorEntity):
    """An = Modul antwortet. Bleibt verfügbar, damit „getrennt“ sichtbar ist."""

    _attr_translation_key = "connection"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: HbwCoordinator) -> None:
        super().__init__(coordinator, "connection")

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        h = self.coordinator.health
        return {
            "last_seen": h.last_seen.isoformat() if h.last_seen else None,
            "offline_since": h.offline_since.isoformat() if h.offline_since else None,
        }
