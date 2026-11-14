"""Gateway-Überwachung: eine Meldung für das Gateway statt einer je Modul."""

from datetime import timedelta

from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
)

from custom_components.hbw_modbus import client as client_mod, health as health_mod
from custom_components.hbw_modbus.const import CONF_DEVICE_ADDRESS, CONF_KIND, DOMAIN, KIND_BUS

from .fake_gateway import FakeSwModule

GW_SENSOR = "sensor.hbw_bus_gateway"


async def _setup(hass: HomeAssistant, gateway) -> tuple[MockConfigEntry, MockConfigEntry]:
    gateway.modules.clear()
    gateway.modules[3] = FakeSwModule()
    gateway.modules[4] = FakeSwModule(stored_id=4)
    entries = []
    for uid, data, title in (
        (f"bus:127.0.0.1:{gateway.port}", {CONF_KIND: KIND_BUS}, "HBW-Bus"),
        (f"127.0.0.1:{gateway.port}:3", {CONF_DEVICE_ADDRESS: 3}, "HBWMB_A03"),
        (f"127.0.0.1:{gateway.port}:4", {CONF_DEVICE_ADDRESS: 4}, "HBWMB_A04"),
    ):
        entry = MockConfigEntry(
            domain=DOMAIN, title=title, unique_id=uid,
            data={CONF_HOST: "127.0.0.1", CONF_PORT: gateway.port, **data},
        )
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        entries.append(entry)
    assert hass.states.get(GW_SENSOR).state == "ok"
    return entries[1], entries[2]


@pytest.fixture
def clock(monkeypatch):
    """Uhr der Integration vorstellen; Timer der Ereignisschleife laufen über
    async_fire_time_changed mit (Netzwerk-Wartezeiten bleiben echt)."""
    state = {"offset": 0.0}
    real_time, real_now = client_mod._clock, health_mod._utcnow
    monkeypatch.setattr(client_mod, "_clock", lambda: real_time() + state["offset"])
    monkeypatch.setattr(health_mod, "_utcnow", lambda: real_now() + timedelta(seconds=state["offset"]))
    return state


async def _later(hass: HomeAssistant, clock, seconds: float) -> None:
    clock["offset"] += seconds
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=clock["offset"]))
    await hass.async_block_till_done(wait_background_tasks=True)


def _issues(hass: HomeAssistant) -> set[str]:
    return {i.translation_key for (d, _), i in ir.async_get(hass).issues.items() if d == DOMAIN}


async def test_gateway_unreachable_one_alarm(hass: HomeAssistant, gateway, clock) -> None:
    await _setup(hass, gateway)
    events = async_capture_events(hass, f"{DOMAIN}_alarm")
    port = gateway.port
    await gateway.stop()
    await _later(hass, clock, 6)
    assert hass.states.get(GW_SENSOR).state == "unreachable"
    for _ in range(4):                     # gut 3 Minuten
        await _later(hass, clock, 50)
    assert _issues(hass) == {"gateway_unreachable"}   # keine Meldung je Modul
    assert [e.data["kind"] for e in events] == ["gateway_offline"]

    await gateway.start(port)
    await _later(hass, clock, 61)        # Module werden offline nur jede Minute gefragt
    assert hass.states.get(GW_SENSOR).state == "ok"
    assert _issues(hass) == set()
    assert [e.data["kind"] for e in events] == ["gateway_offline", "gateway_online"]
    assert hass.states.get("switch.hbwmb_a03_kanal_1").state == "off"


async def test_bus_silent(hass: HomeAssistant, gateway, clock) -> None:
    await _setup(hass, gateway)
    gateway.modules.clear()                # Gateway erreichbar, kein Modul antwortet
    for _ in range(5):
        await _later(hass, clock, 40)
    assert hass.states.get(GW_SENSOR).state == "bus_silent"
    assert _issues(hass) == {"gateway_bus_silent"}


async def test_single_module_offline_still_reported(hass: HomeAssistant, gateway, clock) -> None:
    await _setup(hass, gateway)
    gateway.modules.pop(4)                 # nur ein Modul fällt aus
    for _ in range(5):
        await _later(hass, clock, 40)
    assert hass.states.get(GW_SENSOR).state == "ok"
    assert _issues(hass) == {"module_offline"}
