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
    """Ein Kanal als eigenes Gerät: Coil schreiben, Zustand aus HR2.
    Hauptentität des Kanalgeräts, trägt dessen Namen."""

    _attr_name = None

    def __init__(self, coordinator: HbwCoordinator, channel: int) -> None:
        super().__init__(coordinator, f"channel_{channel}", channel)
        self.channel = channel

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

    async def _write(self, coro, on: bool | None, timer: int = 0) -> None:
        """Befehl senden und den quittierten Zustand sofort anzeigen.
        on=None heißt umschalten. Jeder Schaltbefehl beendet einen laufenden
        Timer im Modul, ein neuer Timer setzt ihn auf `timer`."""
        try:
            await coro
        except ModbusError as err:
            raise HomeAssistantError(f"Kanal {self.channel}: {err}") from err
        outputs = self.coordinator.data.outputs
        if on is None:
            outputs ^= self._bit
        elif on:
            outputs |= self._bit
        else:
            outputs &= ~self._bit
        self.coordinator.note_write(outputs, self.channel, timer)

    async def async_turn_on(self, **kwargs: Any) -> None:
        c = self.coordinator
        await self._write(c.client.write_coil(c.unit, self.channel - 1, True), True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        c = self.coordinator
        await self._write(c.client.write_coil(c.unit, self.channel - 1, False), False)

    async def async_toggle(self, **kwargs: Any) -> None:
        """Umschalten im Modul selbst (HR7), unabhängig vom zuletzt gelesenen Zustand."""
        c = self.coordinator
        await self._write(c.client.write_register(c.unit, HR_TOGGLE, self._bit), None)

    async def async_turn_on_for(self, seconds: int) -> None:
        """Einschalten, das Modul schaltet nach `seconds` Sekunden selbst wieder aus."""
        c = self.coordinator
        await self._write(c.client.write_register(c.unit, HR_TIMER + self.channel - 1, seconds), True, seconds)
