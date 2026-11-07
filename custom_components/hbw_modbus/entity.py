"""Gemeinsame Basis der Entitäten und Geräte.

Aufbau wie bei den bisherigen HM485-Geräten: ein Gerät je Modul
(z. B. „HBWMB_A03“ mit den Diagnosewerten) und ein eigenes Gerät je Kanal
bzw. Eingang („HBWMB_A03 Kanal 1“, „HBWMB_S01 Eingang 1“, Modell
„HBWMB_A03“ von „HM485MB“), verbunden über das Modulgerät. Die Kanalgeräte
lassen sich in HA frei umbenennen.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, KIND_S0, MANUFACTURER
from .coordinator import HbwCoordinator, ModuleInfo


def module_name(unit: int, kind: str) -> str:
    """Name wie die HBW-Nummern: A = Ausgänge (Schalt-/Rollladenmodul,
    Modbus-ID = A-Nummer), S = Zähler (ab ID 101: 101 = S01)."""
    if kind == KIND_S0:
        return f"HBWMB_S{unit - 100 if unit > 100 else unit:02d}"
    return f"HBWMB_A{unit:02d}"


def channel_word(kind: str) -> str:
    return "Eingang" if kind == KIND_S0 else "Kanal"


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


def channel_device_info(
    entry: ConfigEntry, unit: int, kind: str, channel: int, module_device_id: str | None
) -> DeviceInfo:
    assert entry.unique_id is not None
    return DeviceInfo(
        identifiers={channel_identifier(entry, channel)},
        name=f"{entry.title} {channel_word(kind)} {channel}",
        manufacturer=MANUFACTURER,
        model=module_name(unit, kind),
        via_device_id=module_device_id,
    )


def blind_identifier(entry: ConfigEntry) -> tuple[str, str]:
    return (DOMAIN, f"{entry.unique_id}_blind1")


def blind_device_info(entry: ConfigEntry, unit: int, kind: str, module_device_id: str | None) -> DeviceInfo:
    """Rollladen auf zwei Kanälen eines Schaltmoduls als eigenes Gerät."""
    return DeviceInfo(
        identifiers={blind_identifier(entry)},
        name=f"{entry.title} Rollladen",
        manufacturer=MANUFACTURER,
        model=module_name(unit, kind),
        via_device_id=module_device_id,
    )


class HbwEntity(CoordinatorEntity[HbwCoordinator]):
    """Entität eines HBW-MB-Moduls, am Modulgerät oder an einem Kanalgerät."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: HbwCoordinator, key: str, channel: int | None = None, blind: bool = False
    ) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        assert entry is not None and entry.unique_id is not None
        self._attr_unique_id = f"{entry.unique_id}_{key}"
        if blind:
            self._attr_device_info = blind_device_info(
                entry, coordinator.unit, coordinator.info.kind, coordinator.module_device_id
            )
        elif channel is None:
            self._attr_device_info = module_device_info(entry, coordinator.info)
        else:
            self._attr_device_info = channel_device_info(
                entry, coordinator.unit, coordinator.info.kind, channel, coordinator.module_device_id
            )
