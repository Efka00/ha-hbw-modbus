"""Gemeinsame Basis der Entitäten und Geräte.

Aufbau wie bei den bisherigen HM485-Geräten: ein Gerät je Modul
(z. B. „HBWMB_A03“ mit den Diagnosewerten) und ein eigenes Gerät je Kanal
(„HBWMB_A03 Kanal 1“, Modell „HBWMB_A03“ von „HM485MB“), verbunden über das
Modulgerät. Die Kanalgeräte lassen sich in HA frei umbenennen.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import HbwCoordinator, ModuleInfo


def module_name(unit: int) -> str:
    """HBWMB_Axx wie die A-Nummern der HBW-Module (Modbus-ID = A-Nummer)."""
    return f"HBWMB_A{unit:02d}"


def module_device_info(entry: ConfigEntry, info: ModuleInfo) -> DeviceInfo:
    assert entry.unique_id is not None
    return DeviceInfo(
        identifiers={(DOMAIN, entry.unique_id)},
        name=entry.title,
        manufacturer=MANUFACTURER,
        model=info.model,
        sw_version=info.firmware_str,
        serial_number=None if info.hbw_address == 0xFFFFFFFF else f"{info.hbw_address:08X}",
    )


def channel_identifier(entry: ConfigEntry, channel: int) -> tuple[str, str]:
    return (DOMAIN, f"{entry.unique_id}_ch{channel}")


def channel_device_info(entry: ConfigEntry, unit: int, channel: int) -> DeviceInfo:
    assert entry.unique_id is not None
    return DeviceInfo(
        identifiers={channel_identifier(entry, channel)},
        name=f"{entry.title} Kanal {channel}",
        manufacturer=MANUFACTURER,
        model=module_name(unit),
        via_device=(DOMAIN, entry.unique_id),
    )


class HbwEntity(CoordinatorEntity[HbwCoordinator]):
    """Entität eines HBW-MB-Moduls, am Modulgerät oder an einem Kanalgerät."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: HbwCoordinator, key: str, channel: int | None = None) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        assert entry is not None and entry.unique_id is not None
        self._attr_unique_id = f"{entry.unique_id}_{key}"
        if channel is None:
            self._attr_device_info = module_device_info(entry, coordinator.info)
        else:
            self._attr_device_info = channel_device_info(entry, coordinator.unit, channel)
