"""Abfrage eines HBW-MB-Moduls."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field, replace
import logging
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .blindcalc import POS_MAX, BlindCurve
from .client import HbwModbusClient, ModbusConnectionError, ModbusError, ModbusExceptionResponse
from .health import module_health
from .const import (
    DOMAIN,
    BLIND_ARRIVAL_MARGIN,
    BLIND_MIN_CHECK,
    BLIND_START_WAIT,
    BLIND_TICK,
    BUS_RATE_WINDOW,
    FW_BUS_BL,
    FW_BUS_S0,
    FW_BUS_SW,
    FW_DIAG_BL,
    FW_DIAG_SW,
    FW_FORCE_SW,
    HR_FORCE,
    HR_BLIND_ACT,
    HR_CONFIG,
    HR_OUTPUTS,
    HR_TIMER,
    IR_DIAG,
    IR_DIAG_COUNT,
    IR_DIAG_COUNT_BUS,
    IR_INFO,
    IR_INFO_COUNT,
    HR_COUNTS,
    IR_COUNTS,
    IR_MODULE_TYPE,
    IR_PULSE,
    KIND_BLIND,
    KIND_S0,
    KIND_SWITCH,
    MODULE_TYPE_S0,
    OFFLINE_INTERVAL,
    OFFLINE_TIMEOUT,
    SCAN_INTERVAL,
    SCAN_INTERVAL_MOVING,
    BLIND_CLOSING,
    BLIND_OPENING,
    IR_BLIND,
    IR_INFO_COUNT_BL,
    HR_BLIND_PAR,
    HR_BLIND_CMD,
    BLIND_PAR_COUNT,
    BLIND_STRIDE,
    BLIND_VALUES,
)

_LOGGER = logging.getLogger(__name__)


def firmware_str(fw: int) -> str:
    """Version wie in der Firmware: 0x0110 = 1.10 (Stellen hexadezimal)."""
    return f"{fw >> 8:x}.{fw & 0xFF:02x}"


@dataclass
class ModuleInfo:
    """Was ein Modul über sich meldet (IR0..3)."""

    kind: str
    firmware: int
    channels: int
    hbw_address: int

    @property
    def firmware_str(self) -> str:
        return firmware_str(self.firmware)

    @property
    def has_diag(self) -> bool:
        if self.kind == KIND_S0:
            return True
        return self.firmware >= (FW_DIAG_SW if self.kind == KIND_SWITCH else FW_DIAG_BL)

    @property
    def has_bus_stats(self) -> bool:
        """Busstatistik in IR36..38."""
        need = {KIND_SWITCH: FW_BUS_SW, KIND_BLIND: FW_BUS_BL, KIND_S0: FW_BUS_S0}[self.kind]
        return self.firmware >= need

    @property
    def has_force(self) -> bool:
        """Schaltmodul mit „Dauer ein“ je Kanal (HR8)."""
        return self.kind == KIND_SWITCH and self.firmware >= FW_FORCE_SW

    @property
    def model(self) -> str:
        if self.kind == KIND_SWITCH:
            return f"HBW-MB-Sw ({self.channels} Kanäle)"
        if self.kind == KIND_S0:
            return f"HBW-MB-S0 ({self.channels} Eingänge)"
        return f"HBW-MB-Bl ({self.channels} Rollläden)"


@dataclass
class ModuleData:
    """Zustand nach einer Abfrage."""

    outputs: int = 0
    lock_mask: int = 0               # Kanäle eines Rollladens (nicht schaltbar)
    force_mask: int = 0              # Kanäle auf „Dauer ein“ (HR8)
    timers: list[int] = field(default_factory=list)
    info: list[int] = field(default_factory=list)   # IR0..6
    diag: list[int] | None = None                   # IR30..35 (ab Busstatistik IR30..38)
    bus_error_rate: float | None = None   # % gestörte Rahmen in der letzten Stunde
    lost_requests: int = 0    # seit dem Start von HA: Anfragen, die das Modul nie erreicht haben
    lost_responses: int = 0   # seit dem Start von HA: Antworten, die HA nie erreicht haben
    counts: list[int] = field(default_factory=list)     # Zählerstände (Impulse)
    intervals: list[int] = field(default_factory=list)  # ms zwischen den letzten Impulsen
    since: list[int] = field(default_factory=list)      # ms seit dem letzten Impuls
    # Rollläden: beim Rollladenmodul 1..4, beim Schaltmodul höchstens einer
    blinds: list[dict[str, int]] = field(default_factory=list)
    blind_par: list[list[int]] = field(default_factory=list)  # je Rollladen HR10n+2..8

    @property
    def blind_moving(self) -> bool:
        return any(b["status"] in (BLIND_OPENING, BLIND_CLOSING) for b in self.blinds)


@dataclass
class BlindMotion:
    """Laufende Fahrt, verankert an der letzten Rückmeldung des Moduls."""

    t0: float        # time.monotonic() der Rückmeldung
    p0: int          # Position dabei, 0..10000
    up: bool
    target: int      # Ziel 0..10000
    end: float       # berechnete Ankunft (time.monotonic())
    curve: BlindCurve


def _u32(regs: list[int], n: int) -> list[int]:
    return [(regs[2 * i] << 16) | regs[2 * i + 1] for i in range(n)]


async def probe_module(client: HbwModbusClient, unit: int, quick: bool = False) -> ModuleInfo:
    """Modul am Bus erkennen: Version, Kanäle, Art (Schalt-, Rollladen- oder Zählermodul).
    quick: erste Anfrage nur einmal und kurz (Start von HA, Modul evtl. nicht da)."""
    if quick:
        ir = await client.read_registers(unit, True, IR_INFO, 4, timeout=OFFLINE_TIMEOUT, probe=True)
    else:
        ir = await client.read_registers(unit, True, IR_INFO, 4)
    try:
        module_type = (await client.read_registers(unit, True, IR_MODULE_TYPE, 1))[0]
    except ModbusExceptionResponse as err:
        if err.code != 2:
            raise
        module_type = 0   # ältere Firmware ohne IR7
    if module_type == MODULE_TYPE_S0:
        return ModuleInfo(kind=KIND_S0, firmware=ir[0], channels=ir[1], hbw_address=(ir[2] << 16) | ir[3])
    try:
        await client.read_registers(unit, False, 1, 1)   # HR1 gibt es nur beim Schaltmodul
        kind = KIND_SWITCH
    except ModbusExceptionResponse as err:
        if err.code != 2:
            raise
        kind = KIND_BLIND
    return ModuleInfo(kind=kind, firmware=ir[0], channels=ir[1], hbw_address=(ir[2] << 16) | ir[3])


def lock_mask(config: list[int], channels: int) -> int:
    """Rollladen-Kanäle aus HR4/HR5 (wie lockMask() in der Firmware)."""
    act, dirc = config[HR_BLIND_ACT], config[HR_BLIND_ACT + 1]
    if act and dirc and act != dirc and act <= channels and dirc <= channels:
        return (1 << (act - 1)) | (1 << (dirc - 1))
    return 0


class HbwCoordinator(DataUpdateCoordinator[ModuleData]):
    """Fragt ein Modul im festen Takt ab."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: HbwModbusClient,
        unit: int,
        info: ModuleInfo,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {client.host} ID {unit}",
            update_interval=SCAN_INTERVAL,
        )
        self.client = client
        self.unit = unit
        self.info = info
        self._writes = 0   # zählt Schaltbefehle, erkennt überholte Abfragen
        self.module_device_id: str | None = None   # Gerät des Moduls (für die Kanalgeräte)
        # Zählermodul: höchster bekannter Zählerstand je Eingang, in HA gespeichert.
        # Meldet das Modul weniger (Impulse seit dem letzten Speichern im Modul
        # beim Stromausfall verloren), wird der Stand zurückgeschrieben.
        self.known_counts: list[int] = []
        self._store: Store[dict] = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}")
        self._setup_lock: int | None = None   # Rollladen-Kanäle beim Einrichten
        self._reload_scheduled = False
        # Rollladen-Einstellungen ändern sich nur über HA oder eine Einlernfahrt:
        # einmal lesen, danach nur nach dem Ende einer Einlernfahrt (Bus schlank)
        self._blind_par: list[list[int]] | None = None
        self._offline = False   # letzte Abfrage ohne Antwort -> selten und kurz fragen
        self.teach_done: set[int] = set()   # Rollläden (1..n), deren Einlernfahrt gerade fertig wurde
        self.health = module_health(hass, entry)
        # Rollladenfahrten: Position rechnet HA selbst, nachgefragt wird bei Ankunft
        self.blind_motion: dict[int, BlindMotion] = {}
        self._blind_target: dict[int, int] = {}        # Ziel des letzten Fahrbefehls
        self._blind_start_wait: dict[int, float] = {}  # Fahrbefehl gesendet, Anlauf erwartet bis
        self._unsub_arrival = None
        self._unsub_tick = None
        # Busstatistik: Stand der letzten Abfrage, Zuwächse der letzten Stunde
        self._bus_prev: tuple[int, ...] | None = None
        self._bus_window: deque[tuple[float, int, int]] = deque()
        self._bus_rate: float | None = None
        self.lost_requests = 0
        self.lost_responses = 0

    def _module_changed(self, why: str) -> None:
        """Modul hat sich geändert (Firmware, Kanäle, Rollladen): Eintrag neu laden,
        damit Gerät und Entitäten zum neuen Stand passen."""
        if self._reload_scheduled or self.config_entry is None:
            return
        self._reload_scheduled = True
        _LOGGER.info("%s: %s, Integration wird neu geladen", self.name, why)
        self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)

    @property
    def blind_count(self) -> int:
        """Zahl der Rollläden: alle beim Rollladenmodul, 0 oder 1 beim Schaltmodul."""
        if self.info.kind == KIND_BLIND:
            return self.info.channels
        return 1 if self.data is not None and self.data.lock_mask else 0

    async def async_set_blind_par(self, blind: int, index: int, value: int) -> None:
        """Parameter (0 = HR10n+2 … 6 = HR10n+8) von Rollladen n schreiben, sofort anzeigen."""
        await self.client.write_register(self.unit, 10 * blind + HR_BLIND_PAR + index, value, retry=True)
        if self._blind_par is not None and len(self._blind_par) >= blind:
            self._blind_par[blind - 1][index] = value
        if self.data is not None and len(self.data.blind_par) >= blind:
            par = [list(p) for p in self.data.blind_par]
            par[blind - 1][index] = value
            self.async_set_updated_data(replace(self.data, blind_par=par))

    async def _read_blinds(self, data: ModuleData, count: int) -> None:
        b = await self.client.read_registers(
            self.unit, True, IR_BLIND, (BLIND_VALUES - 1) * BLIND_STRIDE + count
        )
        data.blinds = [
            {
                "position": b[i],
                "status": b[BLIND_STRIDE + i],
                "known": b[2 * BLIND_STRIDE + i],
                "teach": b[3 * BLIND_STRIDE + i],
                "moves": b[4 * BLIND_STRIDE + i],
            }
            for i in range(count)
        ]
        old = self.data.blinds if self.data is not None else []
        for i, bl in enumerate(data.blinds):
            if bl["teach"]:
                self.teach_done.discard(i + 1)
            elif i < len(old) and old[i]["teach"] == 5 and bl["known"]:
                self.teach_done.add(i + 1)   # letzter Marker gesetzt, Zeiten gespeichert
        taught = any(i < len(old) and old[i]["teach"] and not b["teach"] for i, b in enumerate(data.blinds))
        if self._blind_par is None or len(self._blind_par) != count or taught:
            self._blind_par = [
                await self.client.read_registers(self.unit, False, 10 * n + HR_BLIND_PAR, BLIND_PAR_COUNT)
                for n in range(1, count + 1)
            ]
        data.blind_par = [list(p) for p in self._blind_par]

    async def async_blind_command(self, blind: int, command: int) -> None:
        """Befehl an Rollladen n (HR10n): 0 zu, 1 auf, 2 Stopp, 10 Einlernen
        starten, 11 Marker, 12 Einlernen abbrechen. Danach sofort neu lesen
        und während der Fahrt jede Sekunde."""
        # Einlernen starten (10) und Marker (11) nicht wiederholen: ein doppelter
        # Marker würde den nächsten Einlernschritt auslösen.
        await self.client.write_register(
            self.unit, 10 * blind + HR_BLIND_CMD, command, retry=command not in (10, 11)
        )
        if command in (0, 1, 2):
            self.note_blind_command(blind, {0: 0, 1: POS_MAX}.get(command))
        else:
            self.update_interval = SCAN_INTERVAL_MOVING   # Einlernen: Schritte sofort sehen
        await self.async_refresh()

    def note_blind_command(self, blind: int, target: int | None) -> None:
        """Vor dem Neulesen nach einem Fahrbefehl: Ziel 0..10000, None = Stopp."""
        if target is None:
            self._blind_target.pop(blind, None)
            self._blind_start_wait.pop(blind, None)
        else:
            self._blind_target[blind] = target
            self._blind_start_wait[blind] = time.monotonic() + BLIND_START_WAIT

    def blind_live_position(self, blind: int) -> int | None:
        """Gerechnete Position (0..100 %) während einer Fahrt, sonst None."""
        m = self.blind_motion.get(blind)
        if m is None:
            return None
        pos = m.curve.position(m.p0, m.up, (time.monotonic() - m.t0) * 1000)
        pos = min(pos, m.target) if m.up else max(pos, m.target)
        return round(pos / 100)

    def _track_blinds(self, data: ModuleData) -> None:
        """Nach jeder Abfrage: Fahrten an der Rückmeldung des Moduls verankern,
        Ankunft berechnen und genau dann wieder nachfragen."""
        now = time.monotonic()
        for i, b in enumerate(data.blinds, 1):
            par = data.blind_par[i - 1] if len(data.blind_par) >= i else None
            moving = b["status"] in (BLIND_OPENING, BLIND_CLOSING) and not b["teach"]
            if moving and par:
                up = b["status"] == BLIND_OPENING
                p0 = b["position"] * 100
                target = self._blind_target.get(i)
                if target is None or (up and target < p0) or (not up and target > p0):
                    target = POS_MAX if up else 0   # z. B. Referenzfahrt über die Endlage
                curve = BlindCurve(par)
                rest = curve.travel_ms(p0, target) + curve.end_extra(target, up)
                self.blind_motion[i] = BlindMotion(now, p0, up, target, now + rest / 1000, curve)
                self._blind_start_wait.pop(i, None)
            else:
                self.blind_motion.pop(i, None)
                if self._blind_start_wait.get(i, 0) <= now:
                    self._blind_start_wait.pop(i, None)
                    self._blind_target.pop(i, None)
        self._schedule_blind_checks(now)

    def _schedule_blind_checks(self, now: float) -> None:
        if self._unsub_arrival:
            self._unsub_arrival()
            self._unsub_arrival = None
        delays = []
        if self.blind_motion:
            arrival = min(m.end for m in self.blind_motion.values())
            delays.append(max(arrival - now + BLIND_ARRIVAL_MARGIN, BLIND_MIN_CHECK))
        if any(t > now for t in self._blind_start_wait.values()):
            delays.append(BLIND_MIN_CHECK)   # Befehl gesendet, Fahrt noch nicht gemeldet
        if delays:
            self._unsub_arrival = async_call_later(self.hass, min(delays), self._blind_check)
        if self.blind_motion and self._unsub_tick is None:
            self._unsub_tick = async_call_later(self.hass, BLIND_TICK, self._blind_tick)

    async def _blind_check(self, _now: Any) -> None:
        self._unsub_arrival = None
        await self.async_refresh()

    @callback
    def _blind_tick(self, _now: Any) -> None:
        """Anzeige während der Fahrt fortschreiben – ohne Busverkehr."""
        self._unsub_tick = None
        if self.blind_motion:
            self.async_update_listeners()
            self._unsub_tick = async_call_later(self.hass, BLIND_TICK, self._blind_tick)

    async def async_shutdown(self) -> None:
        for unsub in (self._unsub_arrival, self._unsub_tick):
            if unsub:
                unsub()
        self._unsub_arrival = self._unsub_tick = None
        await super().async_shutdown()

    async def async_set_force(self, channel: int, on: bool) -> None:
        """Kanal (1..N) auf „Dauer ein“ setzen oder freigeben. Gesetzt geht
        der Kanal sofort an; freigegeben bleibt er an, bis er ausgeschaltet wird."""
        assert self.data is not None
        bit = 1 << (channel - 1)
        mask = (self.data.force_mask | bit) if on else (self.data.force_mask & ~bit)
        await self.client.write_register(self.unit, HR_FORCE, mask, retry=True)
        outputs = self.data.outputs | bit if on else self.data.outputs
        self._writes += 1
        self.async_set_updated_data(replace(self.data, force_mask=mask, outputs=outputs))

    async def async_load_counts(self) -> None:
        stored = await self._store.async_load()
        if stored and isinstance(stored.get("counts"), list):
            self.known_counts = [int(v) for v in stored["counts"]]

    def _save_counts(self) -> None:
        counts = list(self.known_counts)
        self._store.async_delay_save(lambda: {"counts": counts}, 60)

    async def async_set_count(self, channel: int, value: int) -> None:
        """Zählerstand eines Eingangs (1..N) setzen, auch nach unten."""
        await self.client.write_registers(
            self.unit, HR_COUNTS + 2 * (channel - 1), [value >> 16, value & 0xFFFF], retry=True
        )
        self._set_known(channel - 1, value)
        if self.data is not None and len(self.data.counts) >= channel:
            counts = list(self.data.counts)
            counts[channel - 1] = value
            self.async_set_updated_data(replace(self.data, counts=counts))

    def _set_known(self, index: int, value: int) -> None:
        while len(self.known_counts) <= index:
            self.known_counts.append(0)
        self.known_counts[index] = value
        self._save_counts()

    async def _check_counts(self, data: ModuleData) -> None:
        changed = False
        for i, value in enumerate(data.counts):
            known = self.known_counts[i] if i < len(self.known_counts) else 0
            if value < known:
                _LOGGER.warning(
                    "%s: Eingang %d meldet %d statt %d (Neustart?), Zählerstand wird zurückgeschrieben",
                    self.name, i + 1, value, known,
                )
                await self.client.write_registers(
                    self.unit, HR_COUNTS + 2 * i, [known >> 16, known & 0xFFFF], retry=True
                )
                data.counts[i] = known
            elif value > known:
                while len(self.known_counts) <= i:
                    self.known_counts.append(0)
                self.known_counts[i] = value
                changed = True
        if changed:
            self._save_counts()

    def _bus_update(self, crc: int, diag: list[int], sent: int, timeouts: int) -> None:
        """Busstatistik fortschreiben. Zwischen zwei Abfragen hat HA `sent` Anfragen
        an das Modul geschickt (einschließlich der aktuellen), das Modul hat IR37
        um so viele verstandene Anfragen erhöht. Was fehlt, ging auf dem Hinweg
        verloren; ausgebliebene Antworten darüber hinaus auf dem Rückweg."""
        up = (diag[4] << 16) | diag[5]
        frames, own, broken = diag[6], diag[7], diag[8]
        prev, self._bus_prev = self._bus_prev, (up, crc, frames, own, broken, sent, timeouts)
        if prev is None or up < prev[0]:
            return   # erster Wert, nach einem Ausfall oder Neustart des Moduls: nur merken
        d_crc = (crc - prev[1]) & 0xFFFF
        d_frames = (frames - prev[2]) & 0xFFFF
        d_own = (own - prev[3]) & 0xFFFF
        d_broken = (broken - prev[4]) & 0xFFFF
        lost_req = max(0, (sent - prev[5]) - d_own)
        lost_resp = max(0, (timeouts - prev[6]) - lost_req)
        if lost_req or lost_resp:
            _LOGGER.info(
                "%s: %d Anfrage(n) auf dem Hinweg, %d Antwort(en) auf dem Rückweg verloren",
                self.name, lost_req, lost_resp,
            )
        self.lost_requests += lost_req
        self.lost_responses += lost_resp
        now = time.monotonic()
        self._bus_window.append((now, d_crc + d_broken, d_frames))
        while self._bus_window and now - self._bus_window[0][0] > BUS_RATE_WINDOW:
            self._bus_window.popleft()
        bad = sum(w[1] for w in self._bus_window)
        total = bad + sum(w[2] for w in self._bus_window)
        self._bus_rate = round(100 * bad / total, 3) if total else None

    def note_write(self, outputs: int, channel: int, timer: int) -> None:
        """Nach einem quittierten Schaltbefehl den neuen Zustand sofort übernehmen,
        statt auf die nächste Abfrage zu warten."""
        self._writes += 1
        if self.data is None:
            return
        timers = list(self.data.timers)
        if len(timers) >= channel:
            timers[channel - 1] = timer
        self.async_set_updated_data(replace(self.data, outputs=outputs, timers=timers))

    async def _read_outputs(self, data: ModuleData) -> None:
        config = await self.client.read_registers(
            self.unit, False, HR_CONFIG, 9 if self.info.has_force else 8
        )
        data.outputs = config[HR_OUTPUTS]
        data.force_mask = config[HR_FORCE] if self.info.has_force else 0
        data.lock_mask = lock_mask(config, self.info.channels)
        data.timers = await self.client.read_registers(self.unit, False, HR_TIMER, self.info.channels)

    async def _async_update_data(self) -> ModuleData:
        writes = self._writes
        try:
            count = IR_INFO_COUNT_BL if self.info.kind == KIND_BLIND else IR_INFO_COUNT
            if self._offline:
                info = await self.client.read_registers(
                    self.unit, True, IR_INFO, count, timeout=OFFLINE_TIMEOUT, probe=True
                )
            else:
                info = await self.client.read_registers(self.unit, True, IR_INFO, count)
            if self._offline:
                _LOGGER.info("%s antwortet wieder", self.name)
                self._offline = False
            if info[0] != self.info.firmware:
                self._module_changed(f"Firmware {self.info.firmware_str} -> {firmware_str(info[0])}")
            elif info[1] != self.info.channels or ((info[2] << 16) | info[3]) != self.info.hbw_address:
                self._module_changed("anderes Modul unter dieser ID")
            data = ModuleData(info=info)
            if self.info.kind == KIND_S0:
                n = self.info.channels
                data.counts = _u32(await self.client.read_registers(self.unit, True, IR_COUNTS, 2 * n), n)
                pulse = await self.client.read_registers(self.unit, True, IR_PULSE, 4 * n)
                data.intervals = _u32(pulse[: 2 * n], n)
                data.since = _u32(pulse[2 * n :], n)
                await self._check_counts(data)
            if self.info.kind == KIND_SWITCH:
                await self._read_outputs(data)
                if data.lock_mask:
                    await self._read_blinds(data, 1)
                if self._setup_lock is None:
                    self._setup_lock = data.lock_mask
                elif data.lock_mask != self._setup_lock:
                    self._module_changed("Rollladen-Kanäle geändert")
            if self.info.kind == KIND_BLIND:
                await self._read_blinds(data, self.info.channels)
            if self.info.has_bus_stats:
                data.diag = await self.client.read_registers(self.unit, True, IR_DIAG, IR_DIAG_COUNT_BUS)
                # gleich danach, ohne await dazwischen: passt genau zum gelesenen IR37
                self._bus_update(data.info[5], data.diag, self.client.sent.get(self.unit, 0),
                                 self.client.timeouts.get(self.unit, 0))
            elif self.info.has_diag:
                data.diag = await self.client.read_registers(self.unit, True, IR_DIAG, IR_DIAG_COUNT)
            # Während der Abfrage geschaltet: Ausgänge könnten veraltet sein, neu lesen
            for _ in range(3):
                if self.info.kind != KIND_SWITCH or self._writes == writes:
                    break
                writes = self._writes
                await self._read_outputs(data)
        except ModbusConnectionError as err:
            # keine Antwort: bis das Modul wieder antwortet, nur jede Minute fragen
            self._offline = True
            self._bus_prev = None   # Ausfall gehört nicht in die Busstatistik
            self.update_interval = OFFLINE_INTERVAL
            self.health.failure()
            raise UpdateFailed(str(err)) from err
        except ModbusError as err:
            raise UpdateFailed(str(err)) from err
        self.health.success(data.info[5])
        data.bus_error_rate = self._bus_rate
        data.lost_requests = self.lost_requests
        data.lost_responses = self.lost_responses
        # Einlernfahrt: jede Sekunde abfragen. Normale Fahrten rechnet HA selbst
        # und fragt erst bei der berechneten Ankunft nach (_track_blinds).
        teaching = any(b["teach"] for b in data.blinds)
        self.update_interval = SCAN_INTERVAL_MOVING if teaching else SCAN_INTERVAL
        self._track_blinds(data)
        return data
