"""Rollladen auf zwei Kanälen eines Schaltmoduls (HBW-MB-Sw ab v1.06)."""

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
from .const import (
    BLIND_CLOSED,
    BLIND_CLOSING,
    BLIND_OPENING,
    HR_BLIND_CMD,
    HR_BLIND_POS,
    KIND_SWITCH,
    SCAN_INTERVAL_MOVING,
)
from .coordinator import HbwCoordinator
from .entity import HbwEntity

CMD_CLOSE, CMD_OPEN, CMD_STOP = 0, 1, 2


async def async_setup_entry(
    hass: HomeAssistant, entry: HbwConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data.coordinator
    if coordinator.info.kind == KIND_SWITCH and coordinator.data.lock_mask:
        async_add_entities([HbwBlind(coordinator)])


class HbwBlind(HbwEntity, CoverEntity):
    """Rollladen mit Position. Die Fahrlogik (Kennlinie, Schutzpausen,
    Referenzfahrt) läuft im Modul, HA schickt nur Befehle."""

    _attr_name = None
    _attr_device_class = CoverDeviceClass.SHUTTER
    _attr_supported_features = (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.STOP
        | CoverEntityFeature.SET_POSITION
    )

    def __init__(self, coordinator: HbwCoordinator) -> None:
        super().__init__(coordinator, "blind1", blind=True)

    @property
    def _blind(self) -> dict[str, int] | None:
        return self.coordinator.data.blind

    @property
    def available(self) -> bool:
        return super().available and self._blind is not None

    @property
    def current_cover_position(self) -> int | None:
        b = self._blind
        return b["position"] if b and b["known"] else None

    @property
    def is_closed(self) -> bool | None:
        b = self._blind
        if not b:
            return None
        if b["known"]:
            return b["position"] == 0
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

    async def _send(self, register: int, value: int) -> None:
        c = self.coordinator
        try:
            await c.client.write_register(c.unit, register, value)
        except ModbusError as err:
            raise HomeAssistantError(f"Rollladen: {err}") from err
        # Fahrt beginnt: sofort neu lesen und danach jede Sekunde
        c.update_interval = SCAN_INTERVAL_MOVING
        await c.async_refresh()

    async def async_open_cover(self, **kwargs: Any) -> None:
        await self._send(HR_BLIND_CMD, CMD_OPEN)

    async def async_close_cover(self, **kwargs: Any) -> None:
        await self._send(HR_BLIND_CMD, CMD_CLOSE)

    async def async_stop_cover(self, **kwargs: Any) -> None:
        await self._send(HR_BLIND_CMD, CMD_STOP)

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        await self._send(HR_BLIND_POS, int(kwargs[ATTR_POSITION]))
