"""Diagnosewerte eines Moduls, Zählerstand und Leistung beim Zählermodul."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HbwConfigEntry, is_bus
from .discovery import STATUSES, HbwBus
from .client import ModbusError
from .const import (
    CONF_PULSES_PER_KWH,
    DEFAULT_PULSES_PER_KWH,
    DOMAIN,
    KIND_S0,
    NO_PULSE,
    RESET_CAUSES,
)
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
    if is_bus(entry):
        async_add_entities([HbwSearchStatus(entry.runtime_data)])
        return
    coordinator = entry.runtime_data.coordinator
    entities: list[SensorEntity] = [
        HbwSensor(coordinator, desc)
        for desc in DIAG_SENSORS
        if coordinator.info.has_diag or not desc.needs_diag
    ]
    if coordinator.info.kind == KIND_S0:
        ppk = int(entry.options.get(CONF_PULSES_PER_KWH, DEFAULT_PULSES_PER_KWH))
        for ch in range(1, coordinator.info.channels + 1):
            entities.append(HbwEnergySensor(coordinator, ch, ppk))
            entities.append(HbwPowerSensor(coordinator, ch, ppk))
    async_add_entities(entities)


class HbwSensor(HbwEntity, SensorEntity):
    entity_description: HbwSensorDescription

    def __init__(self, coordinator: HbwCoordinator, description: HbwSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> int | float | str | None:
        return self.entity_description.value_fn(self.coordinator.data)



class HbwEnergySensor(HbwEntity, SensorEntity):
    """Zählerstand eines Eingangs in kWh (für das Energie-Dashboard)."""

    _attr_translation_key = "energy"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 3

    def __init__(self, coordinator: HbwCoordinator, channel: int, pulses_per_kwh: int) -> None:
        super().__init__(coordinator, f"energy_{channel}", channel)
        self.channel = channel
        self.ppk = pulses_per_kwh

    @property
    def native_value(self) -> float | None:
        counts = self.coordinator.data.counts
        return counts[self.channel - 1] / self.ppk if len(counts) >= self.channel else None

    @property
    def extra_state_attributes(self) -> dict[str, int]:
        counts = self.coordinator.data.counts
        return {"pulses": counts[self.channel - 1]} if len(counts) >= self.channel else {}

    async def async_set_meter_reading(self, value: float) -> None:
        """Zählerstand in kWh setzen, z. B. auf den Stand des echten Zählers."""
        try:
            await self.coordinator.async_set_count(self.channel, round(value * self.ppk))
        except ModbusError as err:
            raise HomeAssistantError(f"Eingang {self.channel}: {err}") from err


class HbwPowerSensor(HbwEntity, SensorEntity):
    """Aktuelle Leistung aus dem Impulsabstand. Kommen keine Impulse mehr,
    sinkt sie mit der Zeit seit dem letzten Impuls ab."""

    _attr_translation_key = "power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator: HbwCoordinator, channel: int, pulses_per_kwh: int) -> None:
        super().__init__(coordinator, f"power_{channel}", channel)
        self.channel = channel
        self.ppk = pulses_per_kwh

    @property
    def native_value(self) -> float | None:
        d = self.coordinator.data
        if len(d.since) < self.channel:
            return None
        since = d.since[self.channel - 1]
        if since == NO_PULSE:
            return 0.0   # seit dem Start noch kein Impuls
        ms = max(d.intervals[self.channel - 1], since)
        if ms <= 0:
            return None
        return 3_600_000_000 / (self.ppk * ms)


class HbwSearchStatus(SensorEntity):
    """Rückmeldung der Suche am Bus-Gerät: läuft (mit Fortschritt), nichts
    gefunden, neues Modul gefunden. Ergebnis und Zeitpunkt als Attribute."""

    _attr_has_entity_name = True
    _attr_translation_key = "search_status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = STATUSES
    _attr_should_poll = False

    def __init__(self, bus: HbwBus) -> None:
        self._bus = bus
        self._attr_unique_id = f"{bus.entry.unique_id}_search_status"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, bus.entry.unique_id)})

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._bus.add_listener(self.async_write_ha_state))

    @property
    def native_value(self) -> str:
        return self._bus.status

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        return {
            "progress": self._bus.progress,
            "result": self._bus.result,
            "last_search": self._bus.last_search.isoformat() if self._bus.last_search else None,
        }
