"""Suche am Bus: „Neues Modul gefunden“ verschwindet nach der Rückfrage
oder spätestens nach 30 Minuten."""

from datetime import timedelta

from homeassistant.components import persistent_notification
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.hbw_modbus.const import CONF_DEVICE_ADDRESS, CONF_KIND, DOMAIN, KIND_BUS
from custom_components.hbw_modbus.discovery import (
    STATUS_DONE,
    STATUS_FOUND,
    STATUS_IDLE,
    HbwBus,
)

from .fake_gateway import FakeSwModule


async def _bus(hass: HomeAssistant, gateway) -> tuple[MockConfigEntry, HbwBus]:
    gateway.modules.clear()
    gateway.modules[247] = FakeSwModule()
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="HBW-Bus",
        unique_id=f"bus:127.0.0.1:{gateway.port}",
        data={CONF_HOST: "127.0.0.1", CONF_PORT: gateway.port, CONF_KIND: KIND_BUS},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    bus: HbwBus = entry.runtime_data
    bus.start(full=False)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert bus.status == STATUS_FOUND
    assert bus.open_flows() == 1
    return entry, bus


def _notified(hass: HomeAssistant, entry: MockConfigEntry) -> bool:
    return f"{DOMAIN}_search_{entry.entry_id}" in persistent_notification._async_get_or_create_notifications(hass)


def _later(hass: HomeAssistant, **kw) -> None:
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(**kw))


async def test_found_cleared_after_flow(hass: HomeAssistant, gateway) -> None:
    entry, bus = await _bus(hass, gateway)
    assert _notified(hass, entry)
    _later(hass, seconds=20)
    await hass.async_block_till_done()
    assert bus.status == STATUS_FOUND   # Rückfrage noch offen

    # Modul wurde eingerichtet (hier nachgestellt), Rückfrage ist zu
    MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"127.0.0.1:{gateway.port}:5",
        data={CONF_HOST: "127.0.0.1", CONF_PORT: gateway.port, CONF_DEVICE_ADDRESS: 5},
    ).add_to_hass(hass)
    flow = hass.config_entries.flow.async_progress_by_handler(DOMAIN)[0]
    hass.config_entries.flow.async_abort(flow["flow_id"])
    _later(hass, seconds=40)
    await hass.async_block_till_done()
    assert bus.status == STATUS_DONE
    assert bus.result == "Erledigt: eingerichtet ID 5."
    assert not _notified(hass, entry)
    state = hass.states.get("sensor.hbw_bus_suche")
    assert state is not None and state.state == STATUS_DONE


async def test_found_expires_after_30_min(hass: HomeAssistant, gateway) -> None:
    entry, bus = await _bus(hass, gateway)
    for minutes in (10, 20):
        _later(hass, minutes=minutes)
        await hass.async_block_till_done()
        assert bus.status == STATUS_FOUND
    _later(hass, minutes=31)
    await hass.async_block_till_done()
    assert bus.status == STATUS_IDLE
    assert not _notified(hass, entry)
    assert bus._watch == []
