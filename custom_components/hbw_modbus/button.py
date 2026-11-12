"""Knöpfe: am Bus-Gerät nach neuen Modulen suchen, am Rollladen einlernen."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .client import ModbusError
from .const import DOMAIN
from .coordinator import HbwCoordinator
from .discovery import HbwBus
from .entity import HbwEntity

# Einlernfahrt: Befehle an HR10n
TEACH_BUTTONS = (("teach_start", 10), ("teach_marker", 11), ("teach_abort", 12))


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    runtime = entry.runtime_data
    if isinstance(runtime, HbwBus):
        async_add_entities([HbwSearchButton(runtime, "search_new", False), HbwSearchButton(runtime, "scan_bus", True)])
        return
    c = runtime.coordinator
    async_add_entities(
        HbwTeachButton(c, n, key, cmd) for n in range(1, c.blind_count + 1) for key, cmd in TEACH_BUTTONS
    )


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


class HbwTeachButton(HbwEntity, ButtonEntity):
    """Einlernfahrt am Rollladengerät: starten, Marker setzen, abbrechen.
    Was als Nächstes zu tun ist, zeigt der Sensor „Einlernen“."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: HbwCoordinator, blind: int, key: str, command: int) -> None:
        super().__init__(coordinator, f"blind{blind}_{key}", blind=blind)
        self._attr_translation_key = key
        self.blind = blind
        self.command = command

    async def async_press(self) -> None:
        try:
            await self.coordinator.async_blind_command(self.blind, self.command)
        except ModbusError as err:
            raise HomeAssistantError(f"Rollladen: {err}") from err
