"""Relaiskanäle eines Schaltmoduls mit Typ „Licht“ (ein/aus)."""

from __future__ import annotations

from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HbwConfigEntry
from .channel import HbwChannel, channel_type, free_channels
from .const import TYPE_LIGHT


async def async_setup_entry(
    hass: HomeAssistant, entry: HbwConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        HbwChannelLight(coordinator, ch)
        for ch in free_channels(coordinator)
        if channel_type(entry, ch) == TYPE_LIGHT
    )


class HbwChannelLight(HbwChannel, LightEntity):
    """Kanal mit Typ „Licht“: nur ein/aus, wie das Relais."""

    _attr_color_mode = ColorMode.ONOFF
    _attr_supported_color_modes = {ColorMode.ONOFF}
