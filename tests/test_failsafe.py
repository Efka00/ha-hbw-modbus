"""Notbetrieb (HBW-MB-Sw ab v1.12, HBW-MB-Bl ab v1.08): Einstellungen in HA,
Meldung bei einer Auslösung, Sicherung für den Modultausch."""

from datetime import timedelta

from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
)

from custom_components.hbw_modbus.const import CONF_DEVICE_ADDRESS, DOMAIN

from .fake_gateway import FakeBlModule, FakeModule, FakeSwModule


async def _module(hass: HomeAssistant, gateway, unit: int, mod: FakeModule, title: str) -> MockConfigEntry:
    gateway.modules.clear()
    gateway.modules[unit] = mod
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=title,
        unique_id=f"127.0.0.1:{gateway.port}:{unit}",
        data={CONF_HOST: "127.0.0.1", CONF_PORT: gateway.port, CONF_DEVICE_ADDRESS: unit},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _poll(hass: HomeAssistant, seconds: float = 6) -> None:
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))
    await hass.async_block_till_done(wait_background_tasks=True)


async def _select(hass: HomeAssistant, entity_id: str, option: str) -> None:
    await hass.services.async_call(
        "select", "select_option", {"entity_id": entity_id, "option": option}, blocking=True
    )
    await hass.async_block_till_done()


async def test_switch_module_settings(hass: HomeAssistant, gateway) -> None:
    mod = FakeSwModule(firmware=0x0112, blind_act=2, blind_dir=1, fail_on=0b100)
    entry = await _module(hass, gateway, 3, mod, "HBWMB_A03")
    assert hass.states.get("number.hbwmb_a03_notbetrieb_nach").state == "30"
    assert hass.states.get("select.hbwmb_a03_kanal_3_bei_ausfall").state == "on"
    assert hass.states.get("select.hbwmb_a03_kanal_4_bei_ausfall").state == "nothing"
    assert hass.states.get("select.hbwmb_a03_kanal_1_bei_ausfall") is None   # Rollladen-Kanal
    assert hass.states.get("select.hbwmb_a03_rollladen_bei_ausfall").state == "nothing"
    assert hass.states.get("sensor.hbwmb_a03_notbetrieb_ausgelost").state == "0"

    await _select(hass, "select.hbwmb_a03_kanal_4_bei_ausfall", "off")
    assert (mod.fail_on, mod.fail_off) == (0b100, 0b1000)
    await _select(hass, "select.hbwmb_a03_kanal_3_bei_ausfall", "off")
    assert (mod.fail_on, mod.fail_off) == (0, 0b1100)
    i40, i41 = mod.writes.index((6, 40, 0)), mod.writes.index((6, 41, 0b1100))
    assert i40 < i41          # erst aus „ein“ nehmen, dann zu „aus“: nie in beiden
    await _select(hass, "select.hbwmb_a03_kanal_4_bei_ausfall", "nothing")
    assert (mod.fail_on, mod.fail_off) == (0, 0b100)
    assert hass.states.get("select.hbwmb_a03_kanal_3_bei_ausfall").state == "off"

    await _select(hass, "select.hbwmb_a03_rollladen_bei_ausfall", "open")
    assert mod.fail_blind == 1
    await hass.services.async_call(
        "number", "set_value", {"entity_id": "number.hbwmb_a03_notbetrieb_nach", "value": 45}, blocking=True
    )
    await hass.async_block_till_done()
    assert mod.fail_min == 45 and hass.states.get("number.hbwmb_a03_notbetrieb_nach").state == "45"

    backup = entry.runtime_data.coordinator.backup
    assert backup is not None
    regs = backup["registers"]
    assert (regs["9"], regs["19"], regs["40"], regs["41"]) == (45, 1, 0, 0b100)


async def test_blind_module_settings(hass: HomeAssistant, gateway) -> None:
    mod = FakeBlModule(firmware=0x0108)
    mod.blinds[0].fail = 2
    entry = await _module(hass, gateway, 5, mod, "HBWMB_A05")
    assert hass.states.get("select.hbwmb_a05_rollladen_1_bei_ausfall").state == "close"
    assert hass.states.get("select.hbwmb_a05_rollladen_2_bei_ausfall").state == "nothing"
    await _select(hass, "select.hbwmb_a05_rollladen_2_bei_ausfall", "open")
    assert mod.blinds[1].fail == 1 and (6, 29, 1) in mod.writes
    regs = entry.runtime_data.coordinator.backup["registers"]
    assert (regs["9"], regs["19"], regs["29"], regs["39"], regs["49"]) == (30, 2, 1, 0, 0)


async def test_old_firmware_has_no_emergency_entities(hass: HomeAssistant, gateway) -> None:
    await _module(hass, gateway, 3, FakeSwModule(firmware=0x0111), "HBWMB_A03")
    assert hass.states.get("number.hbwmb_a03_notbetrieb_nach") is None
    assert hass.states.get("select.hbwmb_a03_kanal_3_bei_ausfall") is None
    assert hass.states.get("sensor.hbwmb_a03_notbetrieb_ausgelost") is None


async def test_trigger_reported_once(hass: HomeAssistant, gateway) -> None:
    mod = FakeSwModule(firmware=0x0112, fail_on=0b1)
    entry = await _module(hass, gateway, 3, mod, "HBWMB_A03")
    events = async_capture_events(hass, f"{DOMAIN}_alarm")
    await _poll(hass)
    assert events == []

    mod.fail_count = 1                      # HA war weg, das Modul hat geschaltet
    await _poll(hass)
    assert [e.data["kind"] for e in events] == ["emergency"]
    assert "Notbetrieb hat ausgelöst" in events[0].data["message"]
    assert "30 min" in events[0].data["message"]
    assert hass.states.get("sensor.hbwmb_a03_notbetrieb_ausgelost").state == "1"
    await _poll(hass)
    assert len(events) == 1                  # nur einmal

    # Neustart von HA: nichts doppelt melden
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    await _poll(hass)
    assert len(events) == 1

    # Modul neu gestartet (Starts +1, Zähler wieder 0), danach erneut ausgelöst
    mod.diag[2] += 1
    mod.fail_count = 0
    await _poll(hass)
    assert len(events) == 1
    mod.fail_count = 1                       # gleicher Stand wie vorher, aber neuer Start
    await _poll(hass)
    assert [e.data["kind"] for e in events] == ["emergency", "emergency"]

    # Neustart von Modul und HA während des Ausfalls: Modul meldet nach dem
    # Start schon 1 – auch das ist neu
    await hass.config_entries.async_unload(entry.entry_id)
    mod.diag[2] += 1
    mod.fail_count = 1
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(events) == 3
