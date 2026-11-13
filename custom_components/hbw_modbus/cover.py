"""Rollläden: 1..4 am Rollladenmodul (HBW-MB-Bl) oder einer auf zwei Kanälen
eines Schaltmoduls (HBW-MB-Sw ab v1.06). Register von Rollladen n ab 10·n."""

from __future__ import annotations

from typing import Any

from homeassistant.components.cover import (
    ATTR_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HbwConfigEntry
from .client import ModbusError
from .blindcalc import POS_MAX
from .const import (
    BLIND_CLOSED,
    BLIND_CLOSING,
    BLIND_OPENING,
    HR_BLIND_CMD,
    HR_BLIND_POS,
)
from .coordinator import HbwCoordinator
from .entity import HbwEntity

CMD_CLOSE, CMD_OPEN, CMD_STOP = 0, 1, 2


async def async_setup_entry(
    hass: HomeAssistant, entry: HbwConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data.coordinator
    async_add_entities(HbwBlind(coordinator, n) for n in range(1, coordinator.blind_count + 1))


class HbwBlind(HbwEntity, CoverEntity):
    """Rollladen mit Position. Die Fahrlogik (Kennlinie, Schutzpausen,
    Referenzfahrt) läuft im Modul, HA schickt nur Befehle. Während einer Fahrt
    rechnet HA die Position mit derselben Kennlinie selbst fort (blindcalc)
    und fragt das Modul erst bei der berechneten Ankunft wieder ab."""

    _attr_name = None
    _attr_device_class = CoverDeviceClass.SHUTTER
    _attr_supported_features = (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.STOP
        | CoverEntityFeature.SET_POSITION
    )

    def __init__(self, coordinator: HbwCoordinator, blind: int) -> None:
        super().__init__(coordinator, f"blind{blind}", blind=blind)
        self.blind = blind

    @property
    def _blind(self) -> dict[str, int] | None:
        blinds = self.coordinator.data.blinds
        return blinds[self.blind - 1] if len(blinds) >= self.blind else None

    @property
    def available(self) -> bool:
        return super().available and self._blind is not None

    @property
    def current_cover_position(self) -> int | None:
        b = self._blind
        if not b or not b["known"]:
            return None
        live = self.coordinator.blind_live_position(self.blind)
        return b["position"] if live is None else live

    @property
    def is_closed(self) -> bool | None:
        b = self._blind
        if not b:
            return None
        if b["known"]:
            return self.current_cover_position == 0
        return b["status"] == BLIND_CLOSED

    @property
    def is_opening(self) -> bool:
        return bool(self._blind and self._blind["status"] == BLIND_OPENING)

    @property
    def is_closing(self) -> bool:
        return bool(self._blind and self._blind["status"] == BLIND_CLOSING)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        b = self._blind
        if not b:
            return {}
        return {"position_known": bool(b["known"]), "teach_step": b["teach"], "moves": b["moves"]}

    async def _send(self, register: int, value: int, target: int | None) -> None:
        """Befehl senden. target: Ziel 0..10000 (None = Stopp). Danach sofort
        neu lesen: die Rückmeldung verankert die gerechnete Fahrt."""
        c = self.coordinator
        try:
            await c.client.write_register(c.unit, 10 * self.blind + register, value, retry=True)
        except ModbusError as err:
            raise HomeAssistantError(f"Rollladen: {err}") from err
        c.note_blind_command(self.blind, target)
        await c.async_refresh()

    async def async_open_cover(self, **kwargs: Any) -> None:
        await self._send(HR_BLIND_CMD, CMD_OPEN, POS_MAX)

    async def async_close_cover(self, **kwargs: Any) -> None:
        await self._send(HR_BLIND_CMD, CMD_CLOSE, 0)

    async def async_stop_cover(self, **kwargs: Any) -> None:
        await self._send(HR_BLIND_CMD, CMD_STOP, None)

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        pos = int(kwargs[ATTR_POSITION])
        await self._send(HR_BLIND_POS, pos, pos * 100)
