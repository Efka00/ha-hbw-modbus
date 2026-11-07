"""Schlanker Modbus-TCP-Client für die HBW-MB-Module.

Bewusst ohne pymodbus: Der Waveshare RS485 TO ETH im Modus "Modbus TCP to
RTU" schickt die Antworten vom Bus an alle verbundenen TCP-Clients. Antworten
mit fremder Transaktions-ID werden deshalb still verworfen. Alle Anfragen
laufen nacheinander über eine Verbindung je Gateway.
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


class ModbusError(Exception):
    """Allgemeiner Fehler bei einer Modbus-Anfrage."""


class ModbusConnectionError(ModbusError):
    """Gateway nicht erreichbar oder keine Antwort vom Modul."""


class ModbusExceptionResponse(ModbusError):
    """Das Modul hat mit einem Modbus-Ausnahmecode geantwortet."""

    def __init__(self, code: int) -> None:
        super().__init__(f"Modbus-Ausnahme {code}")
        self.code = code


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

    async def _request(self, unit: int, pdu: bytes, retry: bool) -> bytes:
        async with self._lock:
            for attempt in range(2 if retry else 1):
                try:
                    return await self._transact(unit, pdu)
                except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError) as err:
                    await self._close()
                    last = err
            raise ModbusConnectionError(
                f"{self.host}:{self.port} ID {unit}: {type(last).__name__} {last}"
            ) from last

    async def _transact(self, unit: int, pdu: bytes) -> bytes:
        loop = asyncio.get_running_loop()
        if self._writer is None:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), self.timeout
            )
        assert self._reader is not None
        self._tid = (self._tid % 0xFFFF) + 1
        tid = self._tid
        self._writer.write(struct.pack(">HHHB", tid, 0, len(pdu) + 1, unit) + pdu)
        await self._writer.drain()
        deadline = loop.time() + self.timeout
        while True:
            remaining = max(deadline - loop.time(), 0.001)
            header = await asyncio.wait_for(self._reader.readexactly(7), remaining)
            rtid, proto, length, runit = struct.unpack(">HHHB", header)
            if proto != 0 or not 2 <= length <= 260:
                raise OSError("ungültiger MBAP-Kopf")
            body = await asyncio.wait_for(self._reader.readexactly(length - 1), remaining)
            if rtid != tid or runit != unit or (body[0] & 0x7F) != pdu[0]:
                _LOGGER.debug("Fremde Antwort verworfen: tid %s, ID %s", rtid, runit)
                continue
            if body[0] & 0x80:
                raise ModbusExceptionResponse(body[1] if len(body) > 1 else 0)
            return body

    async def read_registers(self, unit: int, input_regs: bool, address: int, count: int) -> list[int]:
        """Holding- (FC03) oder Input-Register (FC04) lesen."""
        fc = FC_READ_INPUT if input_regs else FC_READ_HOLDING
        body = await self._request(unit, struct.pack(">BHH", fc, address, count), retry=True)
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
        """Eine Coil schreiben (FC05). Kein automatischer Wiederholversuch."""
        await self._request(
            unit, struct.pack(">BHH", FC_WRITE_COIL, address, 0xFF00 if on else 0), retry=False
        )

    async def write_register(self, unit: int, address: int, value: int) -> None:
        """Ein Holding-Register schreiben (FC06). Kein automatischer Wiederholversuch,
        weil z. B. HR7 (Umschalten) bei doppelter Ausführung zurückschalten würde."""
        await self._request(
            unit, struct.pack(">BHH", FC_WRITE_REGISTER, address, value & 0xFFFF), retry=False
        )
