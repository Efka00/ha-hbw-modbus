"""HBW Modbus: Homematic-Wired-Homebrew-Module mit Modbus-Firmware (HBW-MB)."""

from __future__ import annotations

from dataclasses import dataclass
import logging

import voluptuous as vol

from homeassistant.config_entries import SOURCE_INTEGRATION_DISCOVERY, ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
    discovery_flow,
    service,
)
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from .client import HbwModbusClient, ModbusError
from .const import (
    ATTR_SECONDS,
    ATTR_VALUE,
    CONF_DEVICE_ADDRESS,
    CONF_KIND,
    DEFAULT_TIMEOUT,
    DOMAIN,
    KIND_BUS,
    MANUFACTURER,
    KIND_BLIND,
    TYPE_SWITCH,
    SERVICE_SET_METER_READING,
    SERVICE_TURN_ON_FOR,
)
from .channel import channel_type, free_channels
from .coordinator import HbwCoordinator, probe_module
from .health import forget_health, module_health
from .discovery import HbwBus, bus_unique_id
from .entity import blind_identifier, channel_identifier, module_device_info

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.COVER,
    Platform.LIGHT,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]
BUS_PLATFORMS: list[Platform] = [Platform.BUTTON, Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Dienst einmal für alle Module registrieren."""
    for domain in (Platform.SWITCH, Platform.LIGHT):
        service.async_register_platform_entity_service(
            hass,
            DOMAIN,
            SERVICE_TURN_ON_FOR,
            entity_domain=domain,
            schema={vol.Required(ATTR_SECONDS): vol.All(cv.positive_int, vol.Range(min=1, max=65535))},
            func="async_turn_on_for",
        )
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_SET_METER_READING,
        entity_domain=Platform.SENSOR,
        entity_device_classes=[SensorDeviceClass.ENERGY],
        schema={vol.Required(ATTR_VALUE): vol.All(vol.Coerce(float), vol.Range(min=0))},
        func="async_set_meter_reading",
    )
    return True


@dataclass
class HbwRuntime:
    coordinator: HbwCoordinator


type HbwConfigEntry = ConfigEntry[HbwRuntime | HbwBus]


def is_bus(entry: ConfigEntry) -> bool:
    return entry.data.get(CONF_KIND) == KIND_BUS


def _gateways(hass: HomeAssistant) -> dict[str, list]:
    return hass.data.setdefault(DOMAIN, {})


def acquire_client(hass: HomeAssistant, host: str, port: int) -> HbwModbusClient:
    """Eine Verbindung je Gateway, von allen Modulen daran gemeinsam genutzt."""
    key = f"{host}:{port}"
    slot = _gateways(hass).get(key)
    if slot is None:
        slot = _gateways(hass)[key] = [HbwModbusClient(host, port, DEFAULT_TIMEOUT), 0]
    slot[1] += 1
    return slot[0]


async def release_client(hass: HomeAssistant, client: HbwModbusClient) -> None:
    key = f"{client.host}:{client.port}"
    slot = _gateways(hass).get(key)
    if slot is None:
        return
    slot[1] -= 1
    if slot[1] <= 0:
        del _gateways(hass)[key]
        await client.close()


async def _async_setup_bus(hass: HomeAssistant, entry: HbwConfigEntry) -> bool:
    """Bus-Eintrag: nur das Gerät mit den Such-Knöpfen, ohne eigene Abfragen."""
    client = acquire_client(hass, entry.data[CONF_HOST], entry.data[CONF_PORT])
    entry.runtime_data = HbwBus(hass, entry, client)
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.unique_id)},
        name=entry.title,
        manufacturer=MANUFACTURER,
        model="RS485-Bus",
        configuration_url=f"http://{entry.data[CONF_HOST]}",
    )
    await hass.config_entries.async_forward_entry_setups(entry, BUS_PLATFORMS)
    return True


def _offer_bus(hass: HomeAssistant, host: str, port: int) -> None:
    """Gibt es zum Gateway noch keinen Bus-Eintrag, ihn als gefunden anbieten."""
    uid = bus_unique_id(host, port)
    if any(e.unique_id == uid for e in hass.config_entries.async_entries(DOMAIN)):
        return
    discovery_flow.async_create_flow(
        hass,
        DOMAIN,
        context={"source": SOURCE_INTEGRATION_DISCOVERY},
        data={CONF_HOST: host, CONF_PORT: port, CONF_KIND: KIND_BUS},
    )


async def async_setup_entry(hass: HomeAssistant, entry: HbwConfigEntry) -> bool:
    if is_bus(entry):
        return await _async_setup_bus(hass, entry)
    client = acquire_client(hass, entry.data[CONF_HOST], entry.data[CONF_PORT])
    unit = entry.data[CONF_DEVICE_ADDRESS]
    try:
        info = await probe_module(client, unit, quick=True)
    except Exception as err:   # noqa: BLE001 – jeder Fehler hier: später erneut versuchen
        await release_client(hass, client)
        module_health(hass, entry).failure()   # fehlt das Modul schon beim Start
        if not isinstance(err, ModbusError):
            _LOGGER.warning("ID %s: unerwarteter Fehler beim Erkennen (%r), neuer Versuch folgt", unit, err)
        raise ConfigEntryNotReady(str(err)) from err
    coordinator = HbwCoordinator(hass, entry, client, unit, info)
    await coordinator.async_load_counts()
    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady:
        await release_client(hass, client)
        raise
    entry.runtime_data = HbwRuntime(coordinator)

    # Modulgerät zuerst anlegen, die Kanalgeräte verweisen darauf (via_device)
    dev_reg = dr.async_get(hass)
    module_device = dev_reg.async_get_or_create(config_entry_id=entry.entry_id, **module_device_info(entry, info))
    coordinator.module_device_id = module_device.id
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Kanalgeräte entfernen, die es nicht mehr gibt (z. B. jetzt Rollladen-Kanäle)
    entry.async_on_unload(entry.add_update_listener(_options_updated))
    # (Rollladenmodul: keine Kanalgeräte, nur die Rollläden)
    locked = coordinator.data.lock_mask
    expected = {(DOMAIN, entry.unique_id)} | {
        blind_identifier(entry, n) for n in range(1, coordinator.blind_count + 1)
    }
    if info.kind != KIND_BLIND:
        expected |= {
            channel_identifier(entry, ch)
            for ch in range(1, info.channels + 1)
            if not (locked >> (ch - 1)) & 1
        }
    for device in dr.async_entries_for_config_entry(dev_reg, entry.entry_id):
        if not device.identifiers & expected:
            dev_reg.async_remove_device(device.id)
    # Typ eines Kanals geändert: Entität der anderen Art (switch./light.) entfernen
    ent_reg = er.async_get(hass)
    for ch in free_channels(coordinator):
        other = Platform.LIGHT if channel_type(entry, ch) == TYPE_SWITCH else Platform.SWITCH
        if eid := ent_reg.async_get_entity_id(other, DOMAIN, f"{entry.unique_id}_channel_{ch}"):
            ent_reg.async_remove(eid)
    _offer_bus(hass, entry.data[CONF_HOST], entry.data[CONF_PORT])
    return True


async def _options_updated(hass: HomeAssistant, entry: HbwConfigEntry) -> None:
    """Optionen geändert (Impulse pro kWh, Typ eines Kanals): neu laden.
    Kurz verzögert, damit mehrere Änderungen hintereinander nur ein
    Neuladen auslösen."""
    pending = hass.data.setdefault(f"{DOMAIN}_reload", {})
    if (cancel := pending.pop(entry.entry_id, None)) is not None:
        cancel()

    @callback
    def _reload(_now) -> None:
        pending.pop(entry.entry_id, None)
        hass.config_entries.async_schedule_reload(entry.entry_id)

    pending[entry.entry_id] = async_call_later(hass, 2, _reload)


async def async_unload_entry(hass: HomeAssistant, entry: HbwConfigEntry) -> bool:
    bus = is_bus(entry)
    unloaded = await hass.config_entries.async_unload_platforms(entry, BUS_PLATFORMS if bus else PLATFORMS)
    if unloaded:
        runtime = entry.runtime_data
        await release_client(hass, runtime.client if isinstance(runtime, HbwBus) else runtime.coordinator.client)
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: HbwConfigEntry) -> None:
    """Eintrag gelöscht: gemerkte Zählerstände (Zählermodul) ebenfalls löschen."""
    if is_bus(entry):
        return
    forget_health(hass, entry)
    await Store(hass, 1, f"{DOMAIN}.{entry.entry_id}").async_remove()
