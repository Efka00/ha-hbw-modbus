"""Gemeinsame Logik eines Relaiskanals – als Schalter oder als Licht."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import HomeAssistantError

from .client import ModbusError
from .const import CONF_CHANNEL_TYPES, HR_TIMER, HR_TOGGLE, KIND_SWITCH, TYPE_SWITCH
from .coordinator import HbwCoordinator
from .entity import HbwEntity


def channel_type(entry: ConfigEntry, channel: int) -> str:
    return entry.options.get(CONF_CHANNEL_TYPES, {}).get(str(channel), TYPE_SWITCH)


def free_channels(coordinator: HbwCoordinator) -> list[int]:
    """Kanäle des Schaltmoduls ohne die Rollladen-Kanäle."""
    if coordinator.info.kind != KIND_SWITCH:
        return []
    locked = coordinator.data.lock_mask
    return [ch for ch in range(1, coordinator.info.channels + 1) if not (locked >> (ch - 1)) & 1]


class HbwChannel(HbwEntity):
    """Ein Relaiskanal: Coil schreiben, Zustand aus HR2. Hauptentität des
    Kanalgeräts, trägt dessen Namen. Als Schalter (switch.py) oder Licht
    (light.py), je nach Einstellung „Typ“ am Kanalgerät."""

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
    def _forced(self) -> bool:
        return bool(self.coordinator.data.force_mask & self._bit)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        timers = self.coordinator.data.timers
        left = timers[self.channel - 1] if len(timers) >= self.channel else 0
        return {"timer_remaining": left, "always_on": self._forced}

    def _check_forced(self) -> None:
        if self._forced:
            raise HomeAssistantError(
                f"Kanal {self.channel} steht auf „Dauer ein“ und lässt sich nicht ausschalten"
            )

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
        self._check_forced()
        c = self.coordinator
        await self._write(c.client.write_coil(c.unit, self.channel - 1, False), False)

    async def async_toggle(self, **kwargs: Any) -> None:
        """Umschalten im Modul selbst (HR7), unabhängig vom zuletzt gelesenen Zustand."""
        self._check_forced()
        c = self.coordinator
        await self._write(c.client.write_register(c.unit, HR_TOGGLE, self._bit), None)

    async def async_turn_on_for(self, seconds: int) -> None:
        """Einschalten, das Modul schaltet nach `seconds` Sekunden selbst wieder aus."""
        self._check_forced()
        c = self.coordinator
        await self._write(c.client.write_register(c.unit, HR_TIMER + self.channel - 1, seconds), True, seconds)
