"""Einrichtung eines HBW-MB-Moduls über die Oberfläche."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from homeassistant.helpers import selector

from .client import HbwModbusClient, ModbusError
from .const import CONF_DEVICE_ADDRESS, DEFAULT_PORT, DEFAULT_TIMEOUT, DOMAIN, KIND_SWITCH
from .coordinator import probe_module


class HbwModbusConfigFlow(ConfigFlow, domain=DOMAIN):
    """Ein Konfigurationseintrag je Modul (Gateway + Modbus-ID)."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = int(user_input[CONF_PORT])
            unit = int(user_input[CONF_DEVICE_ADDRESS])
            await self.async_set_unique_id(f"{host}:{port}:{unit}")
            self._abort_if_unique_id_configured()
            client = HbwModbusClient(host, port, DEFAULT_TIMEOUT)
            try:
                info = await probe_module(client, unit)
            except ModbusError:
                errors["base"] = "cannot_connect"
            else:
                if info.kind != KIND_SWITCH:
                    errors["base"] = "blind_not_supported"
            finally:
                await client.close()
            if not errors:
                name = (user_input.get(CONF_NAME) or "").strip() or f"HBW-MB ID {unit}"
                return self.async_create_entry(
                    title=name,
                    data={CONF_HOST: host, CONF_PORT: port, CONF_DEVICE_ADDRESS: unit},
                )
        defaults = user_input or {}
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): str,
                vol.Required(CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=65535, mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Required(CONF_DEVICE_ADDRESS, default=defaults.get(CONF_DEVICE_ADDRESS, 1)): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=247, mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Optional(CONF_NAME, default=defaults.get(CONF_NAME, "")): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)
