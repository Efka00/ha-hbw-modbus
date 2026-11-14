"""Einstellungen am Kanal- bzw. Rollladengerät: Typ des Kanals (Schalter
oder Licht) und was der Notbetrieb bei einem Ausfall von HA tut."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HbwConfigEntry
from .channel import channel_type, free_channels
from .client import ModbusError
from .const import (
    CHANNEL_TYPES,
    CONF_CHANNEL_TYPES,
    FAIL_BLIND_OPTIONS,
    FAIL_CHANNEL_OPTIONS,
    FAIL_NOTHING,
    FAIL_OFF,
    FAIL_ON,
)
from .coordinator import HbwCoordinator
from .entity import HbwEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: HbwConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data.coordinator
    entities: list[SelectEntity] = [HbwChannelType(coordinator, ch) for ch in free_channels(coordinator)]
    if coordinator.info.has_fail:
        entities += [HbwFailChannel(coordinator, ch) for ch in free_channels(coordinator)]
        entities += [HbwFailBlind(coordinator, n) for n in range(1, coordinator.blind_count + 1)]
    async_add_entities(entities)


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


class HbwFailChannel(HbwEntity, SelectEntity):
    """Notbetrieb: Kanal bei Ausfall von HA einschalten, ausschalten oder
    lassen (HR40/HR41, im Modul gespeichert)."""

    _attr_translation_key = "fail_channel"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = FAIL_CHANNEL_OPTIONS

    def __init__(self, coordinator: HbwCoordinator, channel: int) -> None:
        super().__init__(coordinator, f"channel_{channel}_fail", channel)
        self.channel = channel

    @property
    def current_option(self) -> str | None:
        d = self.coordinator.data
        if d.fail_min is None:
            return None
        bit = 1 << (self.channel - 1)
        return FAIL_ON if d.fail_on & bit else FAIL_OFF if d.fail_off & bit else FAIL_NOTHING

    async def async_select_option(self, option: str) -> None:
        try:
            await self.coordinator.async_set_fail_channel(self.channel, option)
        except ModbusError as err:
            raise HomeAssistantError(f"Kanal {self.channel}: {err}") from err


class HbwFailBlind(HbwEntity, SelectEntity):
    """Notbetrieb: Rollladen bei Ausfall von HA auffahren, zufahren oder
    stehen lassen (HR10n+9, im Modul gespeichert)."""

    _attr_translation_key = "fail_blind"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = FAIL_BLIND_OPTIONS

    def __init__(self, coordinator: HbwCoordinator, blind: int) -> None:
        super().__init__(coordinator, f"blind{blind}_fail", blind=blind)
        self.blind = blind

    @property
    def current_option(self) -> str | None:
        values = self.coordinator.data.fail_blind
        if len(values) < self.blind or values[self.blind - 1] > 2:
            return None
        return FAIL_BLIND_OPTIONS[values[self.blind - 1]]

    async def async_select_option(self, option: str) -> None:
        try:
            await self.coordinator.async_set_fail_blind(self.blind, FAIL_BLIND_OPTIONS.index(option))
        except ModbusError as err:
            raise HomeAssistantError(f"Rollladen: {err}") from err
