"""Einstellungen des Rollladens auf einem Schaltmodul: Fahrzeiten und Kennlinie (HR12..18)."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberEntityDescription, NumberMode
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HbwConfigEntry
from .client import ModbusError
from .const import KIND_SWITCH
from .coordinator import HbwCoordinator
from .entity import HbwEntity


@dataclass(frozen=True, kw_only=True)
class BlindParDescription(NumberEntityDescription):
    index: int          # 0 = HR12 … 6 = HR18
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
    if coordinator.info.kind == KIND_SWITCH and coordinator.data.lock_mask:
        async_add_entities(BlindParNumber(coordinator, d) for d in BLIND_PARS)


class BlindParNumber(HbwEntity, NumberEntity):
    """Ein Rollladen-Parameter, wird im Modul gespeichert."""

    entity_description: BlindParDescription

    def __init__(self, coordinator: HbwCoordinator, description: BlindParDescription) -> None:
        super().__init__(coordinator, f"blind1_{description.key}", blind=True)
        self.entity_description = description

    @property
    def native_value(self) -> float | None:
        par = self.coordinator.data.blind_par
        d = self.entity_description
        if len(par) <= d.index:
            return None
        return par[d.index] / d.scale

    async def async_set_native_value(self, value: float) -> None:
        d = self.entity_description
        try:
            await self.coordinator.async_set_blind_par(d.index, round(value * d.scale))
        except ModbusError as err:
            raise HomeAssistantError(f"Rollladen: {err}") from err
