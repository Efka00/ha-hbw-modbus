"""Nachgebautes Gateway mit HBW-MB-Sw-Modulen als Modbus-TCP-Server.

Verhält sich wie die Firmware (Coils, HR2, HR7 Umschalten, HR20.. Timer,
IR0..6, IR30..35 ab v1.08, Ausnahme 2 für unbekannte Register) und wie der
Waveshare: Auf Wunsch geht vor jeder Antwort eine fremde Antwort mit
anderer Transaktions-ID an den Client.
"""

from __future__ import annotations

import asyncio
import struct
from dataclasses import dataclass, field


@dataclass
class FakeSwModule:
    channels: int = 12
    firmware: int = 0x0107
    outputs: int = 0
    blind_act: int = 0
    blind_dir: int = 0
    timers: dict[int, int] = field(default_factory=dict)
    diag: list[int] = field(default_factory=lambda: [4, 1, 7, 4980, 0, 3725])
    writes: list[tuple[int, int, int]] = field(default_factory=list)

    @property
    def lock(self) -> int:
        a, d = self.blind_act, self.blind_dir
        if a and d and a != d and a <= self.channels and d <= self.channels:
            return (1 << (a - 1)) | (1 << (d - 1))
        return 0

    def hr(self, a: int) -> int | None:
        if a <= 7:
            return [3, 0, self.outputs, 1, self.blind_act, self.blind_dir, 0, 0][a]
        if 20 <= a < 20 + self.channels:
            return self.timers.get(a - 20, 0)
        return None

    def ir(self, a: int) -> int | None:
        if a <= 6:
            return [self.firmware, self.channels, 0x4200, 0x0055, 12, 0, 5][a]
        if self.firmware >= 0x0108 and 30 <= a <= 35:
            return self.diag[a - 30]
        return None

    def write_hr(self, a: int, v: int) -> bool:
        if a == 7:
            self.outputs ^= v & ~self.lock
            return True
        if 20 <= a < 20 + self.channels:
            if (self.lock >> (a - 20)) & 1:
                return False
            ch = a - 20
            if v:
                self.outputs |= 1 << ch
                self.timers[ch] = v
            else:
                self.outputs &= ~(1 << ch)
                self.timers.pop(ch, None)
            return True
        return False


class FakeGateway:
    def __init__(self) -> None:
        self.modules: dict[int, FakeSwModule] = {}
        self.inject_foreign = False
        self.server: asyncio.base_events.Server | None = None
        self.port = 0
        self.requests = 0

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._client, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self.server:
            self.server.close()
            await self.server.wait_closed()

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                hdr = await reader.readexactly(7)
                tid, _, length, unit = struct.unpack(">HHHB", hdr)
                pdu = await reader.readexactly(length - 1)
                self.requests += 1
                mod = self.modules.get(unit)
                if mod is None:
                    continue   # keine Antwort, Client läuft in den Timeout
                resp = self._handle(mod, pdu)
                if self.inject_foreign:
                    foreign = struct.pack(">BB", 0x04, 2) + b"\x12\x34"
                    writer.write(struct.pack(">HHHB", (tid + 999) & 0xFFFF, 0, len(foreign) + 1, unit) + foreign)
                writer.write(struct.pack(">HHHB", tid, 0, len(resp) + 1, unit) + resp)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()

    @staticmethod
    def _handle(mod: FakeSwModule, pdu: bytes) -> bytes:
        fc = pdu[0]
        a, x = struct.unpack(">HH", pdu[1:5])
        exc = bytes([fc | 0x80, 2])
        if fc in (3, 4):
            vals = [(mod.hr if fc == 3 else mod.ir)(a + i) for i in range(x)]
            if None in vals:
                return exc
            return bytes([fc, 2 * x]) + struct.pack(f">{x}H", *vals)
        if fc == 1:
            if a + x > mod.channels:
                return exc
            bits = mod.outputs >> a
            nbytes = (x + 7) // 8
            return bytes([fc, nbytes]) + (bits & ((1 << x) - 1)).to_bytes(nbytes, "little")
        if fc == 5:
            if a >= mod.channels or (mod.lock >> a) & 1:
                return exc
            mod.writes.append((5, a, x))
            mod.timers.pop(a, None)
            if x == 0xFF00:
                mod.outputs |= 1 << a
            else:
                mod.outputs &= ~(1 << a)
            return pdu[:5]
        if fc == 6:
            mod.writes.append((6, a, x))
            return pdu[:5] if mod.write_hr(a, x) else exc
        return bytes([fc | 0x80, 1])
