"""Relaiskanäle eines Schaltmoduls als Schalter, dazu je Kanal „Dauer ein“."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HbwConfigEntry
from .channel import HbwChannel, channel_type, free_channels
from .client import ModbusError
from .const import TYPE_SWITCH
from .coordinator import HbwCoordinator
from .entity import HbwEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: HbwConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data.coordinator
    channels = free_channels(coordinator)
    entities: list[SwitchEntity] = [
        HbwChannelSwitch(coordinator, ch) for ch in channels if channel_type(entry, ch) == TYPE_SWITCH
    ]
    if coordinator.info.has_force:
        entities += [HbwForceSwitch(coordinator, ch) for ch in channels]
    async_add_entities(entities)


class HbwChannelSwitch(HbwChannel, SwitchEntity):
    """Kanal mit Typ „Schalter“."""


class HbwForceSwitch(HbwEntity, SwitchEntity):
    """Einstellung am Kanalgerät: „Dauer ein“ (HR8). Für Leuchten mit
    Zigbee-Lampe, die immer Strom brauchen. Das Modul hält den Kanal dann
    selbst eingeschaltet, auch nach einem Neustart, und ignoriert Ausschalten."""

    _attr_translation_key = "always_on"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: HbwCoordinator, channel: int) -> None:
        super().__init__(coordinator, f"channel_{channel}_always_on", channel)
        self.channel = channel

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.data.force_mask & (1 << (self.channel - 1)))

    async def _set(self, on: bool) -> None:
        try:
            await self.coordinator.async_set_force(self.channel, on)
        except ModbusError as err:
            raise HomeAssistantError(f"Kanal {self.channel}: {err}") from err

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)
