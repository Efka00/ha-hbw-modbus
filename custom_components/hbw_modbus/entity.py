"""Gemeinsame Basis der Entitäten: ein Gerät je Modul."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import HbwCoordinator


class HbwEntity(CoordinatorEntity[HbwCoordinator]):
    """Entität eines HBW-MB-Moduls."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: HbwCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        assert entry is not None and entry.unique_id is not None
        info = coordinator.info
        self._attr_unique_id = f"{entry.unique_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.unique_id)},
            name=entry.title,
            manufacturer="HBW-MB (Homebrew)",
            model=info.model,
            sw_version=info.firmware_str,
            serial_number=None if info.hbw_address == 0xFFFFFFFF else f"{info.hbw_address:08X}",
        )
