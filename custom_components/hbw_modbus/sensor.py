"""Timer-Restzeiten und Diagnosewerte."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfElectricPotential, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HbwConfigEntry
from .const import RESET_CAUSES
from .coordinator import HbwCoordinator, ModuleData
from .entity import HbwEntity


@dataclass(frozen=True, kw_only=True)
class HbwSensorDescription(SensorEntityDescription):
    value_fn: Callable[[ModuleData], int | float | str | None]
    needs_diag: bool = False


def _uptime(d: ModuleData) -> int:
    if d.diag is not None:
        return (d.diag[4] << 16) | d.diag[5]
    return d.info[4] * 60   # ältere Firmware: nur Minuten


def _reset_cause(d: ModuleData) -> str | None:
    assert d.diag is not None
    return RESET_CAUSES[d.diag[0]] if d.diag[0] < len(RESET_CAUSES) else "unknown"


DIAG_SENSORS: tuple[HbwSensorDescription, ...] = (
    HbwSensorDescription(
        key="uptime",
        translation_key="uptime",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_unit_of_measurement=UnitOfTime.HOURS,
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_uptime,
    ),
    HbwSensorDescription(
        key="crc_errors",
        translation_key="crc_errors",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.info[5],
    ),
    HbwSensorDescription(
        key="reset_cause",
        translation_key="reset_cause",
        device_class=SensorDeviceClass.ENUM,
        options=RESET_CAUSES,
        entity_category=EntityCategory.DIAGNOSTIC,
        needs_diag=True,
        value_fn=_reset_cause,
    ),
    HbwSensorDescription(
        key="watchdog_restarts",
        translation_key="watchdog_restarts",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        needs_diag=True,
        value_fn=lambda d: d.diag[1] if d.diag else None,
    ),
    HbwSensorDescription(
        key="boot_count",
        translation_key="boot_count",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        needs_diag=True,
        value_fn=lambda d: d.diag[2] if d.diag else None,
    ),
    HbwSensorDescription(
        key="supply_voltage",
        translation_key="supply_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.MILLIVOLT,
        suggested_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=2,
        entity_category=EntityCategory.DIAGNOSTIC,
        needs_diag=True,
        value_fn=lambda d: (d.diag[3] or None) if d.diag else None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: HbwConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data.coordinator
    entities: list[SensorEntity] = [
        HbwSensor(coordinator, desc)
        for desc in DIAG_SENSORS
        if coordinator.info.has_diag or not desc.needs_diag
    ]
    locked = coordinator.data.lock_mask
    entities += [
        HbwTimerSensor(coordinator, ch)
        for ch in range(1, coordinator.info.channels + 1)
        if not (locked >> (ch - 1)) & 1
    ]
    async_add_entities(entities)


class HbwSensor(HbwEntity, SensorEntity):
    entity_description: HbwSensorDescription

    def __init__(self, coordinator: HbwCoordinator, description: HbwSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> int | float | str | None:
        return self.entity_description.value_fn(self.coordinator.data)


class HbwTimerSensor(HbwEntity, SensorEntity):
    """Restzeit des Ausschalt-Timers eines Kanals (0 = kein Timer)."""

    _attr_translation_key = "timer"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS

    def __init__(self, coordinator: HbwCoordinator, channel: int) -> None:
        super().__init__(coordinator, f"timer_{channel}")
        self.channel = channel
        self._attr_translation_placeholders = {"channel": str(channel)}

    @property
    def native_value(self) -> int | None:
        timers = self.coordinator.data.timers
        return timers[self.channel - 1] if len(timers) >= self.channel else None
