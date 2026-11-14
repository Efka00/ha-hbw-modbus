"""Modultausch: Ein Modul fällt aus, ein frischer Nano unter 247 übernimmt
seine Einstellungen und seine ID. Der Eintrag in HA bleibt derselbe."""

from datetime import timedelta

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.hbw_modbus.const import CONF_DEVICE_ADDRESS, CONF_KIND, DOMAIN, KIND_BUS
from custom_components.hbw_modbus.discovery import STATUS_DONE, HbwBus

from .fake_gateway import FakeBlind, FakeBlModule, FakeModule, FakeS0Module, FakeSwModule


async def _later(hass: HomeAssistant, **kw) -> None:
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(**kw))
    await hass.async_block_till_done(wait_background_tasks=True)


async def _module(hass: HomeAssistant, gateway, unit: int, mod: FakeModule, title: str) -> MockConfigEntry:
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


async def _fail_and_search(hass: HomeAssistant, gateway, unit: int, fresh: FakeModule) -> tuple[HbwBus, dict]:
    """Modul unter `unit` fällt aus, `fresh` meldet sich unter 247, Suche per Knopf."""
    gateway.modules.pop(unit)
    await _later(hass, seconds=6)          # Abfrage ohne Antwort
    fresh.stored_id = 247
    gateway.modules[247] = fresh
    bus_entry = MockConfigEntry(
        domain=DOMAIN,
        title="HBW-Bus",
        unique_id=f"bus:127.0.0.1:{gateway.port}",
        data={CONF_HOST: "127.0.0.1", CONF_PORT: gateway.port, CONF_KIND: KIND_BUS},
    )
    bus_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(bus_entry.entry_id)
    await hass.async_block_till_done()
    bus: HbwBus = bus_entry.runtime_data
    bus.start(full=False)
    await hass.async_block_till_done(wait_background_tasks=True)
    flows = [  # ohne die Rückfrage „Bus hinzufügen“ vom Einrichten des Moduls
        f for f in hass.config_entries.flow.async_progress_by_handler(DOMAIN) if "hbw_new_module" in f["context"]
    ]
    assert len(flows) == 1
    return bus, flows[0]


async def _replace(hass: HomeAssistant, flow: dict, entry: MockConfigEntry) -> dict:
    assert flow["step_id"] == "new_or_replace"
    result = await hass.config_entries.flow.async_configure(flow["flow_id"], {"next_step_id": "replace"})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "replace"
    return await hass.config_entries.flow.async_configure(flow["flow_id"], {"replace_entry": entry.entry_id})


async def test_replace_switch_module(hass: HomeAssistant, gateway) -> None:
    gateway.modules.clear()
    old = FakeSwModule(firmware=0x0111, invert=0b101, force=0b1000, blind_act=2, blind_dir=1)
    old.blinds[0].par = [1881, 1811, 264, 1097, 0, 10, 3]
    entry = await _module(hass, gateway, 3, old, "HBWMB_A03")
    assert hass.states.get("switch.hbwmb_a03_kanal_5").state == "off"

    fresh = FakeSwModule(firmware=0x0111)
    bus, flow = await _fail_and_search(hass, gateway, 3, fresh)
    assert hass.states.get("switch.hbwmb_a03_kanal_5").state == "unavailable"
    result = await _replace(hass, flow, entry)
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "replaced"

    assert gateway.modules[3] is fresh and 247 not in gateway.modules
    assert (fresh.invert, fresh.force, fresh.blind_act, fresh.blind_dir) == (0b101, 0b1000, 2, 1)
    assert fresh.blinds[0].par == [1881, 1811, 264, 1097, 0, 10, 3]
    await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("switch.hbwmb_a03_kanal_5").state == "off"     # wieder da
    assert hass.states.get("cover.hbwmb_a03_rollladen") is not None
    await _later(hass, seconds=20)
    assert bus.status == STATUS_DONE
    assert bus.result == "Erledigt: HBWMB_A03 (ID 3) ersetzt."


async def test_replace_blind_module_after_restart(hass: HomeAssistant, gateway) -> None:
    """Ausfall über einen Neustart von HA hinweg: Sicherung kommt aus dem Speicher."""
    gateway.modules.clear()
    old = FakeBlModule()
    for n, b in enumerate(old.blinds):
        b.par = [1800 + n, 1700 + n, 200 + n, 900 + n, 20, 10, 0]
    entry = await _module(hass, gateway, 6, old, "HBWMB_A06")
    await _later(hass, seconds=15)                 # Sicherung gespeichert
    gateway.modules.pop(6)
    await hass.config_entries.async_reload(entry.entry_id)   # wie ein Neustart ohne Modul
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY

    fresh = FakeBlModule(blinds=[FakeBlind() for _ in range(4)])
    gateway.modules[6] = old   # nur damit _fail_and_search etwas wegnehmen kann
    _, flow = await _fail_and_search(hass, gateway, 6, fresh)
    result = await _replace(hass, flow, entry)
    assert result["reason"] == "replaced"
    assert [b.par for b in fresh.blinds] == [[1800 + n, 1700 + n, 200 + n, 900 + n, 20, 10, 0] for n in range(4)]
    assert gateway.modules[6] is fresh


async def test_replace_counter_module(hass: HomeAssistant, gateway) -> None:
    gateway.modules.clear()
    old = FakeS0Module(debounce=25, interval=30, counts=[1234, 0, 0, 0, 0, 0, 0, 99])
    entry = await _module(hass, gateway, 21, old, "HBWMB_S01")
    fresh = FakeS0Module()
    _, flow = await _fail_and_search(hass, gateway, 21, fresh)
    result = await _replace(hass, flow, entry)
    assert result["reason"] == "replaced"
    assert (fresh.debounce, fresh.interval) == (25, 30)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert fresh.counts[0] == 1234 and fresh.counts[7] == 99   # Zählerstände schreibt HA zurück
    assert entry.state is ConfigEntryState.LOADED


async def test_replace_needs_current_firmware(hass: HomeAssistant, gateway) -> None:
    gateway.modules.clear()
    entry = await _module(hass, gateway, 3, FakeSwModule(firmware=0x0111, invert=1), "HBWMB_A03")
    fresh = FakeSwModule(firmware=0x0110)
    _, flow = await _fail_and_search(hass, gateway, 3, fresh)
    result = await _replace(hass, flow, entry)
    assert result["type"] is FlowResultType.FORM and result["errors"] == {"base": "firmware_older"}
    assert 247 in gateway.modules and fresh.invert == 0     # nichts geschrieben


async def test_no_replace_offer_when_module_answers(hass: HomeAssistant, gateway) -> None:
    """Antwortet das Modul derselben Art, gibt es nur „ID vergeben“."""
    gateway.modules.clear()
    await _module(hass, gateway, 3, FakeSwModule(firmware=0x0111), "HBWMB_A03")
    gateway.modules[3] = gateway.modules[3]   # bleibt erreichbar
    gateway.modules[99] = FakeSwModule()      # Platzhalter, wird gleich entfernt
    _, flow = await _fail_and_search(hass, gateway, 99, FakeSwModule(firmware=0x0111))
    assert flow["step_id"] == "assign_id"
