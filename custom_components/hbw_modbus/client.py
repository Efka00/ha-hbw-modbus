"""Schlanker Modbus-TCP-Client für die HBW-MB-Module.

Bewusst ohne pymodbus: Der Waveshare RS485 TO ETH im Modus "Modbus TCP to
RTU" schickt die Antworten vom Bus an alle verbundenen TCP-Clients. Antworten
mit fremder Transaktions-ID werden deshalb still verworfen. Alle Anfragen
laufen nacheinander über eine Verbindung je Gateway.

Befehle (Schreibzugriffe) haben Vorrang: Wartet eine Abfrage schon länger
als PREEMPT_GRACE auf ihre Antwort – etwa von einem Modul, das nicht
antwortet –, gibt sie die Verbindung für den Befehl frei und wird danach neu
gestellt. Eine verspätete Antwort auf die zurückgestellte Abfrage wird anhand
der Transaktions-ID verworfen. Eine gesunde Antwort (~30 ms) läuft vorher
ungestört durch, so trifft der Befehl nicht auf einen belegten Bus.

Befehle warten kürzer als Abfragen (WRITE_TIMEOUTS). Bleibt die Antwort aus,
wird ein Befehl, der doppelt ausgeführt dasselbe Ergebnis hat (retry=True),
einmal wiederholt.
"""

from __future__ import annotations

import asyncio
import logging
import struct

_LOGGER = logging.getLogger(__name__)

FC_READ_COILS = 0x01
FC_READ_HOLDING = 0x03
FC_READ_INPUT = 0x04
FC_WRITE_COIL = 0x05
FC_WRITE_REGISTER = 0x06
FC_WRITE_REGISTERS = 0x10

# Wartezeit je Versuch bei Befehlen: erst kurz, beim zweiten Versuch länger
# (der Waveshare kann noch mit einer Abfrage an ein stummes Modul belegt sein).
WRITE_TIMEOUTS = (0.5, 1.0)
WRITE_TIMEOUT_ONCE = 1.5   # Befehle ohne Wiederholung (z. B. Umschalten)
RETRY_PAUSE = 0.05
# So lange darf eine Abfrage auf ihre Antwort warten, bevor ein Befehl sie
# unterbricht.
PREEMPT_GRACE = 0.2


class ModbusError(Exception):
    """Allgemeiner Fehler bei einer Modbus-Anfrage."""


class ModbusConnectionError(ModbusError):
    """Gateway nicht erreichbar oder keine Antwort vom Modul."""


class ModbusExceptionResponse(ModbusError):
    """Das Modul hat mit einem Modbus-Ausnahmecode geantwortet."""

    def __init__(self, code: int) -> None:
        super().__init__(f"Modbus-Ausnahme {code}")
        self.code = code


class _Preempted(Exception):
    """Abfrage zugunsten eines Befehls zurückgestellt (wird neu gestellt)."""


class HbwModbusClient:
    """Eine TCP-Verbindung zu einem Modbus-Gateway."""

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self._lock = asyncio.Lock()
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._tid = 0
        self._clean = True   # False, solange eine Antwort halb gelesen ist
        # Vorrang für Befehle: Zahl wartender Befehle und zwei Signale
        self._prio = 0
        self._prio_wanted = asyncio.Event()   # ein Befehl wartet
        self._prio_idle = asyncio.Event()     # kein Befehl wartet
        self._prio_idle.set()

    async def close(self) -> None:
        """Verbindung schließen."""
        async with self._lock:
            await self._close()

    async def _close(self) -> None:
        writer, self._reader, self._writer = self._writer, None, None
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    async def _request(
        self,
        unit: int,
        pdu: bytes,
        retry: bool,
        timeout: float | None = None,
        probe: bool = False,
        priority: bool = False,
    ) -> bytes:
        """probe: Suche nach Modulen. Bleibt die Antwort aus, ist die ID nur frei –
        die Verbindung bleibt dann offen (eine verspätete Antwort wird später als
        fremde Antwort verworfen).
        priority: Befehl. Läuft vor wartenden Abfragen und unterbricht eine
        Abfrage, die gerade auf ihre Antwort wartet."""
        if priority:
            timeouts = WRITE_TIMEOUTS if retry else (timeout or WRITE_TIMEOUT_ONCE,)
            self._prio += 1
            self._prio_idle.clear()
            self._prio_wanted.set()
            try:
                async with self._lock:
                    return await self._attempts(unit, pdu, timeouts, probe, False, True)
            finally:
                self._prio -= 1
                if not self._prio:
                    self._prio_wanted.clear()
                    self._prio_idle.set()
        t = timeout or self.timeout
        timeouts = (t, t) if retry else (t,)
        while True:
            await self._prio_idle.wait()
            async with self._lock:
                if self._prio:
                    continue   # ein Befehl wartet: Vortritt lassen
                try:
                    return await self._attempts(unit, pdu, timeouts, probe, True, False)
                except _Preempted:
                    _LOGGER.debug("ID %s: Abfrage für einen Befehl zurückgestellt", unit)

    async def _attempts(
        self,
        unit: int,
        pdu: bytes,
        timeouts: tuple[float, ...],
        probe: bool,
        preemptible: bool,
        command: bool,
    ) -> bytes:
        """Ein Versuch je Eintrag in timeouts. Bei Suche und Befehlen bleibt die
        Verbindung nach einer ausgebliebenen Antwort offen, solange nichts halb
        gelesen ist – eine verspätete Antwort wird dann als fremde verworfen."""
        for attempt, timeout in enumerate(timeouts):
            if attempt:
                _LOGGER.warning(
                    "ID %s: keine Antwort auf %s, zweiter Versuch",
                    unit, "Befehl" if command else "Abfrage",
                )
                await asyncio.sleep(RETRY_PAUSE)
            try:
                return await self._transact(unit, pdu, timeout, preemptible)
            except asyncio.TimeoutError as err:
                if not ((probe or command) and self._clean and self._writer is not None):
                    await self._close()
                last = err
            except (OSError, asyncio.IncompleteReadError) as err:
                await self._close()
                last = err
        raise ModbusConnectionError(
            f"{self.host}:{self.port} ID {unit}: {type(last).__name__} {last}"
        ) from last

    async def _read_header(self, remaining: float, preemptible: bool) -> bytes:
        """Kopf der Antwort lesen. preemptible: nach PREEMPT_GRACE abbrechen,
        sobald ein Befehl wartet (_Preempted). Der Abbruch ist sicher:
        readexactly entnimmt erst, wenn alle 7 Byte da sind, ein halber Kopf
        bleibt im Puffer."""
        assert self._reader is not None
        if not preemptible:
            return await asyncio.wait_for(self._reader.readexactly(7), remaining)
        read = asyncio.ensure_future(self._reader.readexactly(7))
        grace = min(PREEMPT_GRACE, remaining)
        done, _ = await asyncio.wait({read}, timeout=grace)
        if read in done:
            return read.result()
        remaining -= grace
        wanted = asyncio.ensure_future(self._prio_wanted.wait())
        try:
            if remaining > 0:
                done, _ = await asyncio.wait(
                    {read, wanted}, timeout=remaining, return_when=asyncio.FIRST_COMPLETED
                )
        finally:
            wanted.cancel()
        if read in done:
            return read.result()
        read.cancel()
        try:
            await read
        except asyncio.CancelledError:
            pass
        if wanted in done:
            raise _Preempted
        raise asyncio.TimeoutError

    async def _transact(self, unit: int, pdu: bytes, timeout: float, preemptible: bool = False) -> bytes:
        loop = asyncio.get_running_loop()
        if self._writer is None:
            self._clean = True
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), self.timeout
            )
        assert self._reader is not None
        self._tid = (self._tid % 0xFFFF) + 1
        tid = self._tid
        self._writer.write(struct.pack(">HHHB", tid, 0, len(pdu) + 1, unit) + pdu)
        await self._writer.drain()
        deadline = loop.time() + timeout
        while True:
            remaining = max(deadline - loop.time(), 0.001)
            header = await self._read_header(remaining, preemptible)
            self._clean = False
            rtid, proto, length, runit = struct.unpack(">HHHB", header)
            if proto != 0 or not 2 <= length <= 260:
                raise OSError("ungültiger MBAP-Kopf")
            body = await asyncio.wait_for(self._reader.readexactly(length - 1), remaining)
            self._clean = True
            if rtid != tid or runit != unit or (body[0] & 0x7F) != pdu[0]:
                _LOGGER.debug("Fremde Antwort verworfen: tid %s, ID %s", rtid, runit)
                continue
            if body[0] & 0x80:
                raise ModbusExceptionResponse(body[1] if len(body) > 1 else 0)
            return body

    async def read_registers(
        self,
        unit: int,
        input_regs: bool,
        address: int,
        count: int,
        timeout: float | None = None,
        probe: bool = False,
    ) -> list[int]:
        """Holding- (FC03) oder Input-Register (FC04) lesen. probe: einmalige,
        kurze Anfrage bei der Suche nach Modulen (kein Wiederholversuch)."""
        fc = FC_READ_INPUT if input_regs else FC_READ_HOLDING
        body = await self._request(
            unit, struct.pack(">BHH", fc, address, count), retry=not probe, timeout=timeout, probe=probe
        )
        if len(body) != 2 + 2 * count or body[1] != 2 * count:
            raise ModbusError("Antwort hat die falsche Länge")
        return list(struct.unpack(f">{count}H", body[2:]))

    async def read_coils(self, unit: int, address: int, count: int) -> list[bool]:
        """Coils lesen (FC01)."""
        body = await self._request(unit, struct.pack(">BHH", FC_READ_COILS, address, count), retry=True)
        if len(body) < 2 + (count + 7) // 8:
            raise ModbusError("Antwort hat die falsche Länge")
        return [bool(body[2 + i // 8] >> (i % 8) & 1) for i in range(count)]

    async def write_coil(self, unit: int, address: int, on: bool) -> None:
        """Eine Coil schreiben (FC05). Bleibt die Antwort aus, einmal wiederholt –
        ein- oder ausschalten hat doppelt ausgeführt dasselbe Ergebnis."""
        await self._request(
            unit, struct.pack(">BHH", FC_WRITE_COIL, address, 0xFF00 if on else 0), retry=True, priority=True
        )

    async def write_register(self, unit: int, address: int, value: int, retry: bool = False) -> None:
        """Ein Holding-Register schreiben (FC06). retry=True nur, wenn doppelt
        ausführen dasselbe Ergebnis hat – nicht z. B. für HR7 (Umschalten),
        das sonst zurückschalten würde."""
        await self._request(
            unit, struct.pack(">BHH", FC_WRITE_REGISTER, address, value & 0xFFFF), retry=retry, priority=True
        )

    async def write_registers(self, unit: int, address: int, values: list[int], retry: bool = False) -> None:
        """Mehrere Holding-Register in einer Anfrage schreiben (FC16)."""
        n = len(values)
        pdu = struct.pack(f">BHHB{n}H", FC_WRITE_REGISTERS, address, n, 2 * n, *(v & 0xFFFF for v in values))
        await self._request(unit, pdu, retry=retry, priority=True)
