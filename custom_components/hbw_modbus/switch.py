"""Relaiskanäle eines Schaltmoduls."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HbwConfigEntry
from .client import ModbusError
from .const import HR_TIMER, HR_TOGGLE
from .coordinator import HbwCoordinator
from .entity import HbwEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: HbwConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data.coordinator
    locked = coordinator.data.lock_mask
    async_add_entities(
        HbwChannelSwitch(coordinator, ch)
        for ch in range(1, coordinator.info.channels + 1)
        if not (locked >> (ch - 1)) & 1
    )


class HbwChannelSwitch(HbwEntity, SwitchEntity):
    """Ein Kanal: Coil schreiben, Zustand aus HR2."""

    _attr_translation_key = "channel"

    def __init__(self, coordinator: HbwCoordinator, channel: int) -> None:
        super().__init__(coordinator, f"channel_{channel}")
        self.channel = channel
        self._attr_translation_placeholders = {"channel": str(channel)}

    @property
    def _bit(self) -> int:
        return 1 << (self.channel - 1)

    @property
    def available(self) -> bool:
        return super().available and not self.coordinator.data.lock_mask & self._bit

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.data.outputs & self._bit)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        timers = self.coordinator.data.timers
        left = timers[self.channel - 1] if len(timers) >= self.channel else 0
        return {"timer_remaining": left}

    async def _write(self, coro) -> None:
        try:
            await coro
        except ModbusError as err:
            raise HomeAssistantError(f"Kanal {self.channel}: {err}") from err
        await self.coordinator.async_request_refresh()

    async def async_turn_on(self, **kwargs: Any) -> None:
        c = self.coordinator
        await self._write(c.client.write_coil(c.unit, self.channel - 1, True))

    async def async_turn_off(self, **kwargs: Any) -> None:
        c = self.coordinator
        await self._write(c.client.write_coil(c.unit, self.channel - 1, False))

    async def async_toggle(self, **kwargs: Any) -> None:
        """Umschalten im Modul selbst (HR7), unabhängig vom zuletzt gelesenen Zustand."""
        c = self.coordinator
        await self._write(c.client.write_register(c.unit, HR_TOGGLE, self._bit))

    async def async_turn_on_for(self, seconds: int) -> None:
        """Einschalten, das Modul schaltet nach `seconds` Sekunden selbst wieder aus."""
        c = self.coordinator
        await self._write(c.client.write_register(c.unit, HR_TIMER + self.channel - 1, seconds))
