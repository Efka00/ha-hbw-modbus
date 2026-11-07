"""HBW Modbus: Homematic-Wired-Homebrew-Module mit Modbus-Firmware (HBW-MB)."""

from __future__ import annotations

from dataclasses import dataclass

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, service
from homeassistant.helpers.typing import ConfigType

from .client import HbwModbusClient, ModbusError
from .const import (
    ATTR_SECONDS,
    CONF_DEVICE_ADDRESS,
    DEFAULT_TIMEOUT,
    DOMAIN,
    KIND_SWITCH,
    SERVICE_TURN_ON_FOR,
)
from .coordinator import HbwCoordinator, probe_module

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.SWITCH]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Dienst einmal für alle Module registrieren."""
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_TURN_ON_FOR,
        entity_domain=Platform.SWITCH,
        schema={vol.Required(ATTR_SECONDS): vol.All(cv.positive_int, vol.Range(min=1, max=65535))},
        func="async_turn_on_for",
    )
    return True


@dataclass
class HbwRuntime:
    coordinator: HbwCoordinator


type HbwConfigEntry = ConfigEntry[HbwRuntime]


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


async def async_setup_entry(hass: HomeAssistant, entry: HbwConfigEntry) -> bool:
    client = acquire_client(hass, entry.data[CONF_HOST], entry.data[CONF_PORT])
    unit = entry.data[CONF_DEVICE_ADDRESS]
    try:
        info = await probe_module(client, unit)
    except ModbusError as err:
        await release_client(hass, client)
        raise ConfigEntryNotReady(str(err)) from err
    if info.kind != KIND_SWITCH:
        await release_client(hass, client)
        raise ConfigEntryNotReady("Rollladenmodule werden noch nicht unterstützt")
    coordinator = HbwCoordinator(hass, entry, client, unit, info)
    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady:
        await release_client(hass, client)
        raise
    entry.runtime_data = HbwRuntime(coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HbwConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await release_client(hass, entry.runtime_data.coordinator.client)
    return unloaded
