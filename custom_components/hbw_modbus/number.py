"""Einstellungen je Rollladen: Fahrzeiten und Kennlinie (HR10n+2..8), am
Modul die Wartezeit des Notbetriebs (HR9)."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberEntityDescription, NumberMode
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HbwConfigEntry
from .client import ModbusError
from .const import FAIL_MAX_MIN
from .coordinator import HbwCoordinator
from .entity import HbwEntity


@dataclass(frozen=True, kw_only=True)
class BlindParDescription(NumberEntityDescription):
    index: int          # 0 = HR10n+2 … 6 = HR10n+8
    scale: float = 1.0  # Registerwert = Anzeigewert × scale


SECONDS = {
    "native_unit_of_measurement": UnitOfTime.SECONDS,
    "device_class": NumberDeviceClass.DURATION,
    "scale": 100.0,     # Register in 0,01 s
    "mode": NumberMode.BOX,
    "entity_category": EntityCategory.CONFIG,
}

BLIND_PARS: tuple[BlindParDescription, ...] = (
    BlindParDescription(key="time_up", translation_key="time_up", index=0,
                        native_min_value=1, native_max_value=600, native_step=0.1, **SECONDS),
    BlindParDescription(key="time_down", translation_key="time_down", index=1,
                        native_min_value=1, native_max_value=600, native_step=0.1, **SECONDS),
    BlindParDescription(key="time_lift", translation_key="time_lift", index=2,
                        native_min_value=0, native_max_value=600, native_step=0.1, **SECONDS),
    BlindParDescription(key="time_mid", translation_key="time_mid", index=3,
                        native_min_value=0, native_max_value=600, native_step=0.1, **SECONDS),
    BlindParDescription(key="dead_time", translation_key="dead_time", index=4,
                        native_min_value=0, native_max_value=5, native_step=0.01, **SECONDS),
    BlindParDescription(key="end_surcharge", translation_key="end_surcharge", index=5,
                        native_min_value=0, native_max_value=100, native_step=1,
                        native_unit_of_measurement=PERCENTAGE, mode=NumberMode.BOX,
                        entity_category=EntityCategory.CONFIG),
    BlindParDescription(key="reference_after", translation_key="reference_after", index=6,
                        native_min_value=0, native_max_value=1000, native_step=1,
                        mode=NumberMode.BOX, entity_category=EntityCategory.CONFIG),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: HbwConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data.coordinator
    entities: list[NumberEntity] = [
        BlindParNumber(coordinator, n, d) for n in range(1, coordinator.blind_count + 1) for d in BLIND_PARS
    ]
    if coordinator.info.has_fail:
        entities.append(HbwFailMinutes(coordinator))
    async_add_entities(entities)


class HbwFailMinutes(HbwEntity, NumberEntity):
    """Notbetrieb: so viele Minuten ohne Anfrage von HA, dann schaltet das
    Modul selbst (0 = aus). Im Modul gespeichert."""

    _attr_translation_key = "fail_minutes"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_device_class = NumberDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_native_min_value = 0
    _attr_native_max_value = FAIL_MAX_MIN
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: HbwCoordinator) -> None:
        super().__init__(coordinator, "fail_minutes")

    @property
    def native_value(self) -> int | None:
        return self.coordinator.data.fail_min

    async def async_set_native_value(self, value: float) -> None:
        try:
            await self.coordinator.async_set_fail_min(int(value))
        except ModbusError as err:
            raise HomeAssistantError(f"Notbetrieb: {err}") from err


class BlindParNumber(HbwEntity, NumberEntity):
    """Ein Rollladen-Parameter, wird im Modul gespeichert."""

    entity_description: BlindParDescription

    def __init__(self, coordinator: HbwCoordinator, blind: int, description: BlindParDescription) -> None:
        super().__init__(coordinator, f"blind{blind}_{description.key}", blind=blind)
        self.entity_description = description
        self.blind = blind

    @property
    def native_value(self) -> float | None:
        pars = self.coordinator.data.blind_par
        d = self.entity_description
        if len(pars) < self.blind or len(pars[self.blind - 1]) <= d.index:
            return None
        return pars[self.blind - 1][d.index] / d.scale

    async def async_set_native_value(self, value: float) -> None:
        d = self.entity_description
        try:
            await self.coordinator.async_set_blind_par(self.blind, d.index, round(value * d.scale))
        except ModbusError as err:
            raise HomeAssistantError(f"Rollladen: {err}") from err
