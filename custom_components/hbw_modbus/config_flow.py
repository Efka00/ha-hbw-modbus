"""Einrichtung über die Oberfläche: ein Eintrag je Modul, dazu je Gateway ein
Bus-Eintrag mit den Such-Knöpfen. Gefundene Module werden erst nach Rückfrage
eingerichtet (siehe discovery.py)."""

from __future__ import annotations

import asyncio
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers import selector

from . import acquire_client, release_client
from .client import ModbusConnectionError, ModbusError
from .const import (
    CONF_DEVICE_ADDRESS,
    CONF_KIND,
    CONF_MIN_POWER,
    CONF_PULSES_PER_KWH,
    DEFAULT_MIN_POWER,
    DEFAULT_PORT,
    DEFAULT_PULSES_PER_KWH,
    DOMAIN,
    HR_CONFIG,
    ID_RANGES,
    IR_INFO,
    KIND_BUS,
    KIND_S0,
    SEARCH_TIMEOUT,
    SERVICE_ID,
)
from .coordinator import ModuleInfo, probe_module
from .discovery import (
    CONF_CHANNELS,
    CONF_FIRMWARE,
    CONF_HBW_ADDRESS,
    CONF_STORED_ID,
    bus_unique_id,
    known_units,
    module_unique_id,
    suggest_id,
)
from .entity import module_name

CTX_NEW_MODULE = "hbw_new_module"


def _id_selector(kind: str) -> selector.NumberSelector:
    ids = ID_RANGES[kind]
    return selector.NumberSelector(
        selector.NumberSelectorConfig(min=ids[0], max=ids[-1], mode=selector.NumberSelectorMode.BOX)
    )


class HbwModbusConfigFlow(ConfigFlow, domain=DOMAIN):
    """Ein Konfigurationseintrag je Modul (Gateway + Modbus-ID) bzw. je Bus."""

    VERSION = 1

    def __init__(self) -> None:
        self._disc: dict[str, Any] = {}
        self._info: ModuleInfo | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return HbwOptionsFlow()

    @classmethod
    @callback
    def async_supports_options(cls, config_entry: ConfigEntry) -> bool:
        """Optionen gibt es nur für Zählermodule (Impulse pro kWh)."""
        return config_entry.data.get(CONF_KIND) == KIND_S0

    def _default_host(self) -> str:
        for entry in self._async_current_entries(include_ignore=False):
            if CONF_HOST in entry.data:
                return entry.data[CONF_HOST]
        return ""

    # ------------------------------------------------------------ von Hand

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="user", menu_options=["module", "bus"])

    async def async_step_module(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = int(user_input[CONF_PORT])
            unit = int(user_input[CONF_DEVICE_ADDRESS])
            await self.async_set_unique_id(module_unique_id(host, port, unit))
            self._abort_if_unique_id_configured()
            client = acquire_client(self.hass, host, port)
            try:
                info = await probe_module(client, unit)
            except ModbusError:
                errors["base"] = "cannot_connect"
            finally:
                await release_client(self.hass, client)
            if not errors:
                return self._create_module(host, port, unit, info.kind, user_input.get(CONF_NAME))
        defaults = user_input or {}
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, self._default_host())): str,
                vol.Required(CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=65535, mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Required(CONF_DEVICE_ADDRESS, default=defaults.get(CONF_DEVICE_ADDRESS, 1)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=247, mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Optional(CONF_NAME, default=defaults.get(CONF_NAME, "")): str,
            }
        )
        return self.async_show_form(step_id="module", data_schema=schema, errors=errors)

    async def async_step_bus(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = int(user_input[CONF_PORT])
            await self.async_set_unique_id(bus_unique_id(host, port))
            self._abort_if_unique_id_configured()
            return self._create_bus(host, port)
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=self._default_host()): str,
                vol.Required(CONF_PORT, default=DEFAULT_PORT): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=65535, mode=selector.NumberSelectorMode.BOX)
                ),
            }
        )
        return self.async_show_form(step_id="bus", data_schema=schema)

    def _create_bus(self, host: str, port: int) -> ConfigFlowResult:
        return self.async_create_entry(
            title=f"HBW-Bus {host}" if port == DEFAULT_PORT else f"HBW-Bus {host}:{port}",
            data={CONF_HOST: host, CONF_PORT: port, CONF_KIND: KIND_BUS},
        )

    def _create_module(self, host: str, port: int, unit: int, kind: str, name: str | None) -> ConfigFlowResult:
        title = (name or "").strip() or module_name(unit, kind)
        return self.async_create_entry(
            title=title,
            data={CONF_HOST: host, CONF_PORT: port, CONF_DEVICE_ADDRESS: unit, CONF_KIND: kind},
        )

    # ------------------------------------------------------------ gefunden

    async def async_step_integration_discovery(self, discovery_info: dict[str, Any]) -> ConfigFlowResult:
        """Von der Suche am Bus (Knopf) oder beim Einrichten eines Moduls (Bus)."""
        self._disc = discovery_info
        host, port = discovery_info[CONF_HOST], discovery_info[CONF_PORT]
        kind = discovery_info[CONF_KIND]
        if kind == KIND_BUS:
            await self.async_set_unique_id(bus_unique_id(host, port))
            self._abort_if_unique_id_configured()
            self.context["title_placeholders"] = {"name": f"HBW-Bus {host}"}
            return await self.async_step_bus_confirm()
        unit = discovery_info[CONF_DEVICE_ADDRESS]
        self._info = ModuleInfo(
            kind=kind,
            firmware=discovery_info[CONF_FIRMWARE],
            channels=discovery_info[CONF_CHANNELS],
            hbw_address=discovery_info[CONF_HBW_ADDRESS],
        )
        if unit == SERVICE_ID:
            # Ohne feste unique_id: Ein „Ignorieren“ würde sonst alle künftigen
            # neuen Module verstecken. Doppelte Rückfragen selbst verhindern.
            key = f"{host}:{port}"
            if any(
                flow["context"].get(CTX_NEW_MODULE) == key
                for flow in self._async_in_progress(include_uninitialized=True)
            ):
                return self.async_abort(reason="already_in_progress")
            self.context[CTX_NEW_MODULE] = key
            self.context["title_placeholders"] = {"name": f"{self._info.model}, ohne ID"}
            return await self.async_step_assign_id()
        await self.async_set_unique_id(module_unique_id(host, port, unit))
        self._abort_if_unique_id_configured()
        self.context["title_placeholders"] = {"name": f"{module_name(unit, kind)} ({self._info.model})"}
        return await self.async_step_discovery_confirm()

    async def async_step_bus_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        host, port = self._disc[CONF_HOST], self._disc[CONF_PORT]
        if user_input is not None:
            return self._create_bus(host, port)
        self._set_confirm_only()
        return self.async_show_form(step_id="bus_confirm", description_placeholders={"host": host})

    def _placeholders(self) -> dict[str, str]:
        assert self._info is not None
        hbw = self._info.hbw_address
        return {
            "model": self._info.model,
            "firmware": self._info.firmware_str,
            "hbw_address": "keine" if hbw == 0xFFFFFFFF else f"{hbw:08X}",
            "unit": str(self._disc[CONF_DEVICE_ADDRESS]),
        }

    async def async_step_discovery_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Modul mit eigener ID gefunden: nur noch bestätigen (Name änderbar)."""
        assert self._info is not None
        host, port, unit = self._disc[CONF_HOST], self._disc[CONF_PORT], self._disc[CONF_DEVICE_ADDRESS]
        if user_input is not None:
            return self._create_module(host, port, unit, self._info.kind, user_input.get(CONF_NAME))
        schema = vol.Schema({vol.Optional(CONF_NAME, default=module_name(unit, self._info.kind)): str})
        return self.async_show_form(
            step_id="discovery_confirm", data_schema=schema, description_placeholders=self._placeholders()
        )

    async def async_step_assign_id(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Modul unter ID 247 gefunden: ID vergeben, ins Modul schreiben, einrichten."""
        assert self._info is not None
        host, port = self._disc[CONF_HOST], self._disc[CONF_PORT]
        stored = self._disc.get(CONF_STORED_ID)
        errors: dict[str, str] = {}
        if user_input is not None:
            new_id = int(user_input[CONF_DEVICE_ADDRESS])
            if new_id not in ID_RANGES[self._info.kind]:
                errors[CONF_DEVICE_ADDRESS] = "id_range"
            elif new_id in known_units(self.hass, host, port, False) and new_id != stored:
                errors[CONF_DEVICE_ADDRESS] = "id_configured"
            else:
                errors = await self._write_id(host, port, new_id)
            if not errors:
                await self.async_set_unique_id(module_unique_id(host, port, new_id), raise_on_progress=False)
                self._abort_if_unique_id_configured()
                return self._create_module(host, port, new_id, self._info.kind, user_input.get(CONF_NAME))
        default_id = (
            int(user_input[CONF_DEVICE_ADDRESS])
            if user_input
            else suggest_id(self.hass, host, port, self._info.kind, stored)
        )
        schema = vol.Schema(
            {
                vol.Required(CONF_DEVICE_ADDRESS, default=default_id): _id_selector(self._info.kind),
                vol.Optional(CONF_NAME, default=(user_input or {}).get(CONF_NAME, "")): str,
            }
        )
        return self.async_show_form(
            step_id="assign_id",
            data_schema=schema,
            errors=errors,
            description_placeholders=self._placeholders(),
        )

    async def _write_id(self, host: str, port: int, new_id: int) -> dict[str, str]:
        """Neue ID nur schreiben, wenn sie am Bus frei ist; danach prüfen, ob das
        Modul unter ihr antwortet."""
        client = acquire_client(self.hass, host, port)
        try:
            try:
                await client.read_registers(new_id, True, IR_INFO, 1, timeout=SEARCH_TIMEOUT, probe=True)
            except ModbusConnectionError:
                pass      # niemand antwortet: ID ist frei
            except ModbusError:
                return {CONF_DEVICE_ADDRESS: "id_in_use"}
            else:
                return {CONF_DEVICE_ADDRESS: "id_in_use"}
            try:
                await client.write_register(SERVICE_ID, HR_CONFIG, new_id)
            except ModbusError:
                return {"base": "no_answer"}
            await asyncio.sleep(0.3)
            try:
                await probe_module(client, new_id)
            except ModbusError:
                return {"base": "assign_failed"}
            return {}
        finally:
            await release_client(self.hass, client)


class HbwOptionsFlow(OptionsFlow):
    """Zählermodul: Impulse pro kWh und Mindestleistung (gelten für alle Eingänge)."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_PULSES_PER_KWH: int(user_input[CONF_PULSES_PER_KWH]),
                    CONF_MIN_POWER: int(user_input[CONF_MIN_POWER]),
                }
            )
        opts = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_PULSES_PER_KWH, default=opts.get(CONF_PULSES_PER_KWH, DEFAULT_PULSES_PER_KWH)
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=100000, mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Required(
                    CONF_MIN_POWER, default=opts.get(CONF_MIN_POWER, DEFAULT_MIN_POWER)
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=1000, mode=selector.NumberSelectorMode.BOX, unit_of_measurement="W"
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
