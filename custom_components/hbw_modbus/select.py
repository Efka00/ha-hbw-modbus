"""Einstellung am Kanalgerät: Typ des Kanals (Schalter oder Licht)."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HbwConfigEntry
from .channel import channel_type, free_channels
from .const import CHANNEL_TYPES, CONF_CHANNEL_TYPES
from .coordinator import HbwCoordinator
from .entity import HbwEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: HbwConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data.coordinator
    async_add_entities(HbwChannelType(coordinator, ch) for ch in free_channels(coordinator))


class HbwChannelType(HbwEntity, SelectEntity):
    """Schalter oder Licht. Eine Änderung lädt den Eintrag neu; die
    Entität behält ihren Namen, nur die Art (switch./light.) wechselt."""

    _attr_translation_key = "channel_type"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = CHANNEL_TYPES

    def __init__(self, coordinator: HbwCoordinator, channel: int) -> None:
        super().__init__(coordinator, f"channel_{channel}_type", channel)
        self.channel = channel

    @property
    def current_option(self) -> str:
        assert self.coordinator.config_entry is not None
        return channel_type(self.coordinator.config_entry, self.channel)

    async def async_select_option(self, option: str) -> None:
        entry = self.coordinator.config_entry
        assert entry is not None
        types = dict(entry.options.get(CONF_CHANNEL_TYPES, {}))
        types[str(self.channel)] = option
        self.hass.config_entries.async_update_entry(
            entry, options={**entry.options, CONF_CHANNEL_TYPES: types}
        )
