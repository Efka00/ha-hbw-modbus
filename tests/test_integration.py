"""Einrichtung, Entitäten und Schaltbefehle gegen ein nachgebautes Modul."""

from datetime import timedelta

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.hbw_modbus.const import CONF_DEVICE_ADDRESS, DOMAIN

from .fake_gateway import FakeSwModule


async def _flow(hass: HomeAssistant, port: int, unit: int = 3, name: str = "arduinohbwtest"):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    return await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_HOST: "127.0.0.1", CONF_PORT: port, CONF_DEVICE_ADDRESS: unit, CONF_NAME: name},
    )


async def _setup(hass: HomeAssistant, port: int) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="arduinohbwtest",
        unique_id=f"127.0.0.1:{port}:3",
        data={CONF_HOST: "127.0.0.1", CONF_PORT: port, CONF_DEVICE_ADDRESS: 3},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_config_flow_creates_entry(hass: HomeAssistant, gateway) -> None:
    result = await _flow(hass, gateway.port, name="")
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "HBWMB_A03"
    assert result["data"] == {CONF_HOST: "127.0.0.1", CONF_PORT: gateway.port, CONF_DEVICE_ADDRESS: 3}
    # zweites Mal dasselbe Modul: abgelehnt
    again = await _flow(hass, gateway.port)
    assert again["type"] is FlowResultType.ABORT and again["reason"] == "already_configured"


async def test_config_flow_no_answer(hass: HomeAssistant, gateway) -> None:
    result = await _flow(hass, gateway.port, unit=9)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_device_and_entities(hass: HomeAssistant, gateway) -> None:
    entry = await _setup(hass, gateway.port)
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_device(identifiers={(DOMAIN, entry.unique_id)})
    assert device is not None
    assert device.name == "arduinohbwtest"
    assert device.manufacturer == "HM485MB"
    assert device.sw_version == "1.07"
    assert device.model == "HBW-MB-Sw (12 Kanäle)"
    sensors = er.async_entries_for_device(er.async_get(hass), device.id)
    assert len(sensors) == 2           # Laufzeit, CRC (v1.07 ohne Diagnose)
    # je Kanal ein eigenes Gerät, verbunden über das Modul
    channel = dev_reg.async_get_device(identifiers={(DOMAIN, f"{entry.unique_id}_ch1")})
    assert channel is not None
    assert channel.name == "arduinohbwtest Kanal 1"
    assert channel.model == "HBWMB_A03" and channel.manufacturer == "HM485MB"
    assert channel.via_device_id == device.id
    assert len(dr.async_entries_for_config_entry(dev_reg, entry.entry_id)) == 13
    assert hass.states.get("switch.arduinohbwtest_kanal_1").state == "off"
    assert hass.states.get("sensor.arduinohbwtest_laufzeit") is not None


async def test_switching(hass: HomeAssistant, gateway) -> None:
    await _setup(hass, gateway.port)
    mod = gateway.modules[3]
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.arduinohbwtest_kanal_3"}, blocking=True)
    await hass.async_block_till_done()
    assert mod.outputs == 0b100
    assert hass.states.get("switch.arduinohbwtest_kanal_3").state == "on"
    # Umschalten geht über HR7 im Modul
    await hass.services.async_call("switch", "toggle", {"entity_id": "switch.arduinohbwtest_kanal_3"}, blocking=True)
    await hass.async_block_till_done()
    assert mod.writes[-1] == (6, 7, 0b100)
    assert mod.outputs == 0
    assert hass.states.get("switch.arduinohbwtest_kanal_3").state == "off"
    # Einschalten mit Timer
    await hass.services.async_call(
        DOMAIN, "turn_on_for", {"entity_id": "switch.arduinohbwtest_kanal_2", "seconds": 300}, blocking=True
    )
    await hass.async_block_till_done()
    assert mod.writes[-1] == (6, 21, 300)
    assert hass.states.get("switch.arduinohbwtest_kanal_2").state == "on"
    assert hass.states.get("switch.arduinohbwtest_kanal_2").attributes["timer_remaining"] == 300


async def test_foreign_responses_ignored(hass: HomeAssistant, gateway) -> None:
    gateway.inject_foreign = True
    await _setup(hass, gateway.port)
    mod = gateway.modules[3]
    mod.outputs = 0b1
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=6))
    await hass.async_block_till_done()
    assert hass.states.get("switch.arduinohbwtest_kanal_1").state == "on"
    assert hass.states.get("sensor.arduinohbwtest_laufzeit").state != "unavailable"


async def test_unavailable_and_recovery(hass: HomeAssistant, gateway) -> None:
    await _setup(hass, gateway.port)
    mod = gateway.modules.pop(3)        # Modul antwortet nicht mehr
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=6))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get("switch.arduinohbwtest_kanal_1").state == "unavailable"
    gateway.modules[3] = mod
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=20))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get("switch.arduinohbwtest_kanal_1").state == "off"


async def test_diag_with_v108_and_blind_lock(hass: HomeAssistant, gateway) -> None:
    gateway.modules[3] = FakeSwModule(firmware=0x0108, blind_act=2, blind_dir=1)
    await _setup(hass, gateway.port)
    assert hass.states.get("switch.arduinohbwtest_kanal_1") is None     # Rollladen-Kanäle fehlen
    assert hass.states.get("switch.arduinohbwtest_kanal_2") is None
    assert hass.states.get("switch.arduinohbwtest_kanal_3") is not None
    assert hass.states.get("sensor.arduinohbwtest_neustart_grund").state == "watchdog"
    assert hass.states.get("sensor.arduinohbwtest_watchdog_neustarte").state == "1"
    assert hass.states.get("sensor.arduinohbwtest_starts").state == "7"
    assert float(hass.states.get("sensor.arduinohbwtest_betriebsspannung").state) == pytest.approx(4.98)
    assert float(hass.states.get("sensor.arduinohbwtest_laufzeit").state) == pytest.approx(3725 / 3600, abs=0.01)
