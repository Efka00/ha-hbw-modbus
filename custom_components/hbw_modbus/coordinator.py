"""Abfrage eines HBW-MB-Moduls."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import HbwModbusClient, ModbusError, ModbusExceptionResponse
from .const import (
    DOMAIN,
    FW_DIAG_BL,
    FW_DIAG_SW,
    HR_BLIND_ACT,
    HR_CONFIG,
    HR_OUTPUTS,
    HR_TIMER,
    IR_DIAG,
    IR_DIAG_COUNT,
    IR_INFO,
    IR_INFO_COUNT,
    KIND_BLIND,
    KIND_SWITCH,
    SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class ModuleInfo:
    """Was ein Modul über sich meldet (IR0..3)."""

    kind: str
    firmware: int
    channels: int
    hbw_address: int

    @property
    def firmware_str(self) -> str:
        return f"{self.firmware >> 8}.{self.firmware & 0xFF:02d}"

    @property
    def has_diag(self) -> bool:
        return self.firmware >= (FW_DIAG_SW if self.kind == KIND_SWITCH else FW_DIAG_BL)

    @property
    def model(self) -> str:
        if self.kind == KIND_SWITCH:
            return f"HBW-MB-Sw ({self.channels} Kanäle)"
        return f"HBW-MB-Bl ({self.channels} Rollläden)"


@dataclass
class ModuleData:
    """Zustand nach einer Abfrage."""

    outputs: int = 0
    lock_mask: int = 0               # Kanäle eines Rollladens (nicht schaltbar)
    timers: list[int] = field(default_factory=list)
    info: list[int] = field(default_factory=list)   # IR0..6
    diag: list[int] | None = None                   # IR30..35


async def probe_module(client: HbwModbusClient, unit: int) -> ModuleInfo:
    """Modul am Bus erkennen: Version, Kanäle, Art (Schalt- oder Rollladenmodul)."""
    ir = await client.read_registers(unit, True, IR_INFO, 4)
    try:
        await client.read_registers(unit, False, 1, 1)   # HR1 gibt es nur beim Schaltmodul
        kind = KIND_SWITCH
    except ModbusExceptionResponse as err:
        if err.code != 2:
            raise
        kind = KIND_BLIND
    return ModuleInfo(kind=kind, firmware=ir[0], channels=ir[1], hbw_address=(ir[2] << 16) | ir[3])


def lock_mask(config: list[int], channels: int) -> int:
    """Rollladen-Kanäle aus HR4/HR5 (wie lockMask() in der Firmware)."""
    act, dirc = config[HR_BLIND_ACT], config[HR_BLIND_ACT + 1]
    if act and dirc and act != dirc and act <= channels and dirc <= channels:
        return (1 << (act - 1)) | (1 << (dirc - 1))
    return 0


class HbwCoordinator(DataUpdateCoordinator[ModuleData]):
    """Fragt ein Modul im festen Takt ab."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: HbwModbusClient,
        unit: int,
        info: ModuleInfo,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {client.host} ID {unit}",
            update_interval=SCAN_INTERVAL,
        )
        self.client = client
        self.unit = unit
        self.info = info

    async def _async_update_data(self) -> ModuleData:
        try:
            info = await self.client.read_registers(self.unit, True, IR_INFO, IR_INFO_COUNT)
            data = ModuleData(info=info)
            if self.info.kind == KIND_SWITCH:
                config = await self.client.read_registers(self.unit, False, HR_CONFIG, 8)
                data.outputs = config[HR_OUTPUTS]
                data.lock_mask = lock_mask(config, self.info.channels)
                data.timers = await self.client.read_registers(
                    self.unit, False, HR_TIMER, self.info.channels
                )
            if self.info.has_diag:
                data.diag = await self.client.read_registers(self.unit, True, IR_DIAG, IR_DIAG_COUNT)
        except ModbusError as err:
            raise UpdateFailed(str(err)) from err
        return data
