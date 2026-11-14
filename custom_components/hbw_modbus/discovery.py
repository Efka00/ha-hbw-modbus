"""Suche nach Modulen am Bus – nur auf Knopfdruck.

Ein Modbus-Modul antwortet nur, wenn es gefragt wird. Damit der Bus schlank
bleibt, fragt die Integration nichts von sich aus ab: Am Bus-Gerät gibt es
zwei Knöpfe.

* „Neues Modul suchen“: eine Anfrage an die Service-ID 247. Unter ihr meldet
  sich ein frisch geflashter Nano (oder ein Modul, dessen Taster beim
  Einschalten gedrückt war).
* „Bus durchsuchen“: die vergebenen IDs 1–19 (Schalt-/Rollladenmodule),
  21–29 (Zähler) und 247, außer denen, die in HA schon eingerichtet sind.
  Je freie ID höchstens 0,3 s, zusammen höchstens etwa 10 s.

Gefundene Module erscheinen in HA als „Neues Gerät gefunden“ und werden erst
nach Rückfrage eingerichtet. Einem Modul mit ID 247 wird dabei eine ID
vergeben (Vorschlag: nächste freie ID für seine Art).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.config_entries import (
    SOURCE_IGNORE,
    SOURCE_INTEGRATION_DISCOVERY,
    ConfigEntry,
    ConfigEntryState,
)
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import discovery_flow
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.util import dt as dt_util

from .client import HbwModbusClient, ModbusError, ModbusExceptionResponse
from .const import (
    CONF_DEVICE_ADDRESS,
    CONF_KIND,
    DOMAIN,
    HR_CONFIG,
    ID_RANGES,
    SCAN_IDS,
    IR_INFO,
    PROBE_TIMEOUT,
    SEARCH_TIMEOUT,
    SERVICE_ID,
)
from .coordinator import ModuleInfo, probe_module

_LOGGER = logging.getLogger(__name__)

CONF_STORED_ID = "stored_id"
CONF_FIRMWARE = "firmware"
CONF_CHANNELS = "channels"
CONF_HBW_ADDRESS = "hbw_address"
CTX_NEW_MODULE = "hbw_new_module"   # Kontext einer Rückfrage für ein Modul ohne ID (247)

# Zustände des Sensors „Suche“ am Bus-Gerät
STATUS_IDLE = "idle"          # bereit: noch nicht gesucht oder Meldung abgelaufen
STATUS_RUNNING = "running"
STATUS_NOTHING = "nothing"    # nichts Neues gefunden
STATUS_FOUND = "found"        # mindestens ein neues Modul (Rückfrage offen)
STATUS_DONE = "done"          # alle Rückfragen erledigt (eingerichtet oder ignoriert)
STATUS_OTHER = "other"        # es hat sich etwas gemeldet, das HA nicht einrichten kann
STATUS_ERROR = "error"
STATUSES = [STATUS_IDLE, STATUS_RUNNING, STATUS_NOTHING, STATUS_FOUND, STATUS_DONE, STATUS_OTHER, STATUS_ERROR]

# „Neues Modul gefunden“ bleibt stehen, bis die Rückfragen erledigt sind
# (HA-intern geprüft, kein Busverkehr), höchstens aber FOUND_TIMEOUT.
FOUND_CHECK = timedelta(seconds=15)
FOUND_TIMEOUT = timedelta(minutes=30)


def module_unique_id(host: str, port: int, unit: int) -> str:
    return f"{host}:{port}:{unit}"


def bus_unique_id(host: str, port: int) -> str:
    return f"bus:{host}:{port}"


def known_units(hass: HomeAssistant, host: str, port: int, ignored: bool) -> set[int]:
    """IDs der Module an diesem Gateway, die in HA eingerichtet sind
    (ignored=False) bzw. die in HA ignoriert wurden (ignored=True)."""
    prefix = f"{host}:{port}:"
    units: set[int] = set()
    for entry in hass.config_entries.async_entries(DOMAIN):
        if (entry.source == SOURCE_IGNORE) != ignored:
            continue
        uid = entry.unique_id or ""
        if uid.startswith(prefix) and uid[len(prefix):].isdigit():
            units.add(int(uid[len(prefix):]))
    return units


def suggest_id(hass: HomeAssistant, host: str, port: int, kind: str, stored_id: int | None) -> int:
    """Vorschlag für die neue ID eines Moduls mit ID 247: die im Modul
    gespeicherte ID (Service-Modus), sonst die nächste freie seiner Art."""
    ids = ID_RANGES[kind]
    if stored_id is not None and stored_id in ids:
        return stored_id
    used = known_units(hass, host, port, False) | known_units(hass, host, port, True)
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is not ConfigEntryState.LOADED:
            continue
        bus = getattr(entry, "runtime_data", None)
        if isinstance(bus, HbwBus) and bus.host == host and bus.port == port:
            used |= bus.seen
    return next((u for u in ids if u not in used), ids[0])


def kind_label(info: ModuleInfo) -> str:
    return info.model


class HbwBus:
    """Der Bus an einem Gateway: Suche nach neuen Modulen."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: HbwModbusClient) -> None:
        self.hass = hass
        self.entry = entry
        self.client = client
        self.host: str = entry.data[CONF_HOST]
        self.port: int = entry.data[CONF_PORT]
        self.seen: set[int] = set()        # bei der letzten Suche gefundene IDs
        self._task: asyncio.Task | None = None
        # Rückmeldung für den Sensor „Suche“
        self.status = STATUS_IDLE
        self.progress: str | None = None   # z. B. "12/29" während der Suche
        self.result: str | None = None     # Ergebnis der letzten Suche
        self.last_search: datetime | None = None
        self._listeners: list[Callable[[], None]] = []
        self._watch: list[Callable[[], None]] = []   # Abmelder der Beobachtung „gefunden“
        self._configured_before: set[int] = set()
        self.replaced: dict[int, str] = {}   # ID -> Name: als Ersatz eingesetzt (config_flow)
        entry.async_on_unload(self._stop_watch)

    def add_listener(self, update: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(update)
        return lambda: self._listeners.remove(update)

    def _set(self, status: str, progress: str | None = None, result: str | None = None) -> None:
        self.status, self.progress = status, progress
        if result is not None:
            self.result = result
        for update in list(self._listeners):
            update()

    @property
    def busy(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def _notification_id(self) -> str:
        return f"{DOMAIN}_search_{self.entry.entry_id}"

    def _notify(self, message: str) -> None:
        persistent_notification.async_create(
            self.hass, message, title=f"{self.entry.title}: Suche", notification_id=self._notification_id
        )

    # ------------------------------------------- „gefunden“ wieder abräumen

    def open_flows(self) -> int:
        """Offene Rückfragen „Neues Gerät gefunden“ für Module an diesem Bus."""
        prefix, key = f"{self.host}:{self.port}:", f"{self.host}:{self.port}"
        return sum(
            1
            for flow in self.hass.config_entries.flow.async_progress_by_handler(DOMAIN)
            if (flow["context"].get("unique_id") or "").startswith(prefix)
            or flow["context"].get(CTX_NEW_MODULE) == key
        )

    def _start_watch(self) -> None:
        self._stop_watch()
        self._watch = [
            async_track_time_interval(self.hass, self._check_found, FOUND_CHECK),
            async_call_later(self.hass, FOUND_TIMEOUT, self._found_timeout),
        ]

    def _stop_watch(self) -> None:
        for unsub in self._watch:
            unsub()
        self._watch = []

    @callback
    def _check_found(self, _now: datetime | None = None) -> None:
        if self.status != STATUS_FOUND:
            self._stop_watch()
            return
        if self.open_flows():
            return
        self._stop_watch()
        added = sorted(known_units(self.hass, self.host, self.port, False) - self._configured_before)
        done = [f"eingerichtet {', '.join(f'ID {u}' for u in added)}"] if added else []
        done += [f"{name} (ID {u}) ersetzt" for u, name in sorted(self.replaced.items())]
        result = (
            f"Erledigt: {'; '.join(done)}." if done else "Erledigt: Rückfrage geschlossen, nichts eingerichtet."
        )
        persistent_notification.async_dismiss(self.hass, self._notification_id)
        self._set(STATUS_DONE, result=result)

    @callback
    def _found_timeout(self, _now: datetime) -> None:
        self._stop_watch()
        if self.status == STATUS_FOUND:
            persistent_notification.async_dismiss(self.hass, self._notification_id)
            self._set(STATUS_IDLE)

    def start(self, full: bool) -> None:
        """Suche im Hintergrund starten (der Knopf kehrt sofort zurück)."""
        if self.busy:
            self._notify("Es läuft bereits eine Suche.")
            return
        self._task = self.entry.async_create_background_task(
            self.hass, self._run_safe(full), f"{DOMAIN} Suche {self.host}"
        )

    async def _run_safe(self, full: bool) -> None:
        try:
            await self._run(full)
        except Exception as err:  # noqa: BLE001 – Rückmeldung statt stiller Abbruch
            _LOGGER.exception("Suche am Bus %s fehlgeschlagen", self.host)
            self.last_search = dt_util.utcnow()
            self._set(STATUS_ERROR, result=f"Suche fehlgeschlagen: {err}")
            self._notify(f"Suche fehlgeschlagen: {err}")

    async def _run(self, full: bool) -> None:
        self._stop_watch()
        configured = known_units(self.hass, self.host, self.port, False)
        self._configured_before = configured
        self.replaced = {}
        ignored = known_units(self.hass, self.host, self.port, True)
        if full:
            units = [u for u in SCAN_IDS if u not in configured]
            self._notify(f"Bus wird durchsucht ({len(units)} IDs, eingerichtete Module werden übersprungen) …")
        else:
            units = [SERVICE_ID]
        lines: list[str] = []
        found_new = 0
        for done, unit in enumerate(units):
            self._set(STATUS_RUNNING, f"{done}/{len(units)}")
            try:
                await self.client.read_registers(
                    unit, True, IR_INFO, 1, timeout=PROBE_TIMEOUT if full else SEARCH_TIMEOUT, probe=True
                )
            except ModbusExceptionResponse:
                lines.append(f"- ID {unit}: antwortet, ist aber kein HBW-MB-Modul")
                continue
            except ModbusError:
                continue
            try:
                info = await probe_module(self.client, unit)
                stored = None
                if unit == SERVICE_ID:
                    stored = (await self.client.read_registers(unit, False, HR_CONFIG, 1))[0]
            except ModbusError as err:
                lines.append(f"- ID {unit}: antwortet nicht vollständig ({err})")
                continue
            self.seen.add(unit)
            label = kind_label(info)
            if unit in ignored:
                lines.append(f"- ID {unit}: {label} – in HA ignoriert")
                continue
            if unit == SERVICE_ID:
                lines.append(
                    f"- {label} ohne eigene ID (247) – ID vergeben oder als Ersatz einsetzen unter „Geräte & Dienste“"
                )
            else:
                lines.append(f"- ID {unit}: {label} – hinzufügen unter „Geräte & Dienste“")
            found_new += 1
            self._start_flow(unit, info, stored)
        if full:
            head = (
                f"Bus durchsucht: {found_new} neue(s) Modul(e)."
                if found_new
                else "Bus durchsucht: keine neuen Module."
            )
            if configured:
                head += f" Schon eingerichtet: {', '.join(str(u) for u in sorted(configured))}."
        elif found_new or lines:
            head = "Neues Modul gefunden:" if found_new else "Unter ID 247 meldet sich:"
        else:
            head = (
                "Kein neues Modul gefunden: Unter ID 247 meldet sich niemand. "
                "Ein neuer Nano muss am Bus hängen und Strom haben."
            )
        message = "\n".join([head, *lines])
        self.last_search = dt_util.utcnow()
        status = STATUS_FOUND if found_new else STATUS_OTHER if lines else STATUS_NOTHING
        self._set(status, result=message)
        self._notify(message)
        if status == STATUS_FOUND:
            self._start_watch()

    def _start_flow(self, unit: int, info: ModuleInfo, stored: int | None) -> None:
        data: dict[str, Any] = {
            CONF_HOST: self.host,
            CONF_PORT: self.port,
            CONF_DEVICE_ADDRESS: unit,
            CONF_KIND: info.kind,
            CONF_FIRMWARE: info.firmware,
            CONF_CHANNELS: info.channels,
            CONF_HBW_ADDRESS: info.hbw_address,
            CONF_STORED_ID: stored,
        }
        discovery_flow.async_create_flow(
            self.hass, DOMAIN, context={"source": SOURCE_INTEGRATION_DISCOVERY}, data=data
        )
