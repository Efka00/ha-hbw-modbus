"""Nachgebautes Gateway mit HBW-MB-Modulen als Modbus-TCP-Server.

Verhält sich wie die Firmware, soweit die Integration sie nutzt:

* Schaltmodul (HBW-MB-Sw): Coils, HR0..7 (HR8 „Dauer ein“ ab v1.10),
  HR20.. Timer, ein Rollladen auf zwei Kanälen (HR4/5, HR10..18, IR10..29).
* Rollladenmodul (HBW-MB-Bl): HR10n..10n+8 und IR10..29 für 1..4 Rollläden,
  kein HR1 (daran erkennt die Integration die Art), kein IR6.
* Beide: IR0..5 (Sw auch IR6), IR30..35 Diagnose (Sw ab v1.08, Bl ab v1.05),
  IR36..38 Busstatistik (Sw ab v1.11, Bl ab v1.07), kein IR7, Ausnahme 2 für
  unbekannte Register.
* HR0 schreiben vergibt eine neue ID: Das Modul antwortet noch unter der
  alten und hört danach auf die neue (wie unter der Service-ID 247).

Und wie der Waveshare: Auf Wunsch geht vor jeder Antwort eine fremde Antwort
mit anderer Transaktions-ID an den Client.
"""

from __future__ import annotations

import asyncio
import struct
from dataclasses import dataclass, field


@dataclass
class FakeBlind:
    position: int = 100           # %, 0 = zu
    status: int = 1               # 0 zu, 1 offen, 2 öffnet, 3 schließt
    known: int = 1
    teach: int = 0
    moves: int = 0
    par: list[int] = field(default_factory=lambda: [2000, 1900, 0, 0, 0, 10, 0])   # HR10n+2..8

    def command(self, v: int) -> bool:
        if v == 0:
            self.position, self.status = 0, 0
        elif v == 1:
            self.position, self.status = 100, 1
        elif v not in (2, 3, 10, 11, 12):
            return False
        return True

    def goto(self, v: int) -> bool:
        if v > 100:
            return False
        self.position, self.status = v, 1 if v else 0
        return True


@dataclass
class FakeModule:
    """Gemeinsames beider Modularten."""

    channels: int = 12
    firmware: int = 0x0107
    stored_id: int = 3            # HR0
    hbw_address: int = 0x42000055
    diag: list[int] = field(default_factory=lambda: [4, 1, 7, 4980, 0, 3725])   # IR30..35
    blinds: list[FakeBlind] = field(default_factory=list)
    writes: list[tuple[int, int, int]] = field(default_factory=list)
    # Busstatistik IR36..38, zählt das Gateway mit
    frames: int = 0
    own: int = 0
    broken: int = 0
    new_id: int | None = None     # nach HR0 schreiben: ab der nächsten Anfrage gültig

    diag_from = 0x0108
    bus_from = 0x0111
    info_count = 7

    def ir(self, a: int) -> int | None:
        info = [self.firmware, self.channels, self.hbw_address >> 16, self.hbw_address & 0xFFFF, 12, 0, 5]
        if a < self.info_count:
            return info[a]
        if 10 <= a < 30 and self.blinds:
            block, i = divmod(a - 10, 4)
            if i >= len(self.blinds):
                return 0
            b = self.blinds[i]
            return [b.position, b.status, b.known, b.teach, b.moves][block]
        if self.firmware >= self.diag_from and 30 <= a <= 35:
            return self.diag[a - 30]
        if self.firmware >= self.bus_from and 36 <= a <= 38:
            return [self.frames, self.own, self.broken][a - 36] & 0xFFFF
        return None

    def blind_hr(self, a: int) -> int | None:
        n, r = divmod(a, 10)
        if not 1 <= n <= len(self.blinds) or r > 8:
            return None
        b = self.blinds[n - 1]
        return b.status if r == 0 else b.position if r == 1 else b.par[r - 2]

    def write_blind_hr(self, a: int, v: int) -> bool:
        n, r = divmod(a, 10)
        if not 1 <= n <= len(self.blinds) or r > 8:
            return False
        b = self.blinds[n - 1]
        if r == 0:
            return b.command(v)
        if r == 1:
            return b.goto(v)
        b.par[r - 2] = v
        return True

    def hr(self, a: int) -> int | None:
        if a == 0:
            return self.stored_id
        return self.blind_hr(a) if a >= 10 else None

    def write_hr(self, a: int, v: int) -> bool:
        if a == 0:
            if not 1 <= v <= 247:
                return False
            self.stored_id = self.new_id = v
            return True
        return self.write_blind_hr(a, v) if a >= 10 else False

    # Coils gibt es nur beim Schaltmodul
    def read_coils(self, a: int, x: int) -> int | None:
        return None

    def write_coil(self, a: int, on: bool) -> bool:
        return False


@dataclass
class FakeSwModule(FakeModule):
    outputs: int = 0
    invert: int = 0
    blind_act: int = 0
    blind_dir: int = 0
    force: int = 0                # HR8 ab v1.10
    timers: dict[int, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.lock and not self.blinds:
            self.blinds = [FakeBlind()]

    @property
    def lock(self) -> int:
        a, d = self.blind_act, self.blind_dir
        if a and d and a != d and a <= self.channels and d <= self.channels:
            return (1 << (a - 1)) | (1 << (d - 1))
        return 0

    @property
    def has_force(self) -> bool:
        return self.firmware >= 0x0110

    def hr(self, a: int) -> int | None:
        if a <= 7:
            return [self.stored_id, self.invert, self.outputs, 1, self.blind_act, self.blind_dir, 0, 0][a]
        if a == 8 and self.has_force:
            return self.force
        if 20 <= a < 20 + self.channels:
            return self.timers.get(a - 20, 0)
        if 10 <= a < 20 and self.lock:
            return self.blind_hr(a)
        return None

    def _switch(self, ch: int, on: bool) -> None:
        self.timers.pop(ch, None)
        if on:
            self.outputs |= 1 << ch
        elif not (self.force >> ch) & 1:
            self.outputs &= ~(1 << ch)

    def write_hr(self, a: int, v: int) -> bool:
        if a in (0, 1):
            if a == 1:
                self.invert = v
                return True
            return super().write_hr(a, v)
        if a == 7:
            self.outputs ^= v & ~self.lock & ~self.force
            return True
        if a == 8 and self.has_force:
            self.force = v
            self.outputs |= v
            return True
        if 20 <= a < 20 + self.channels:
            ch = a - 20
            if (self.lock >> ch) & 1:
                return False
            self._switch(ch, bool(v))
            if v:
                self.timers[ch] = v
            return True
        if 10 <= a < 20 and self.lock:
            return self.write_blind_hr(a, v)
        return False

    def read_coils(self, a: int, x: int) -> int | None:
        if a + x > self.channels:
            return None
        return (self.outputs >> a) & ((1 << x) - 1)

    def write_coil(self, a: int, on: bool) -> bool:
        if a >= self.channels or (self.lock >> a) & 1:
            return False
        self._switch(a, on)
        return True


@dataclass
class FakeBlModule(FakeModule):
    channels: int = 4
    firmware: int = 0x0107
    stored_id: int = 5

    diag_from = 0x0105
    bus_from = 0x0107
    info_count = 6

    def __post_init__(self) -> None:
        if not self.blinds:
            self.blinds = [FakeBlind() for _ in range(self.channels)]


class FakeGateway:
    def __init__(self) -> None:
        self.modules: dict[int, FakeModule] = {}
        self.inject_foreign = False
        self.server: asyncio.base_events.Server | None = None
        self.port = 0
        self.requests = 0
        self._writers: set[asyncio.StreamWriter] = set()

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._client, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self.server:
            self.server.close()
            for w in list(self._writers):   # sonst wartet wait_closed() auf HA
                w.close()
            await self.server.wait_closed()

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._writers.add(writer)
        try:
            while True:
                hdr = await reader.readexactly(7)
                tid, _, length, unit = struct.unpack(">HHHB", hdr)
                pdu = await reader.readexactly(length - 1)
                self.requests += 1
                for mod in self.modules.values():
                    mod.frames += 1
                mod = self.modules.get(unit)
                if mod is None:
                    continue   # keine Antwort, Client läuft in den Timeout
                mod.own += 1
                resp = self._handle(mod, pdu)
                if self.inject_foreign:
                    foreign = struct.pack(">BB", 0x04, 2) + b"\x12\x34"
                    writer.write(struct.pack(">HHHB", (tid + 999) & 0xFFFF, 0, len(foreign) + 1, unit) + foreign)
                writer.write(struct.pack(">HHHB", tid, 0, len(resp) + 1, unit) + resp)
                await writer.drain()
                if mod.new_id is not None:   # neue ID gilt nach der Antwort
                    self.modules[mod.new_id] = self.modules.pop(unit)
                    mod.new_id = None
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            self._writers.discard(writer)
            writer.close()

    @staticmethod
    def _handle(mod: FakeModule, pdu: bytes) -> bytes:
        fc = pdu[0]
        a, x = struct.unpack(">HH", pdu[1:5])
        exc = bytes([fc | 0x80, 2])
        if fc in (3, 4):
            vals = [(mod.hr if fc == 3 else mod.ir)(a + i) for i in range(x)]
            if None in vals:
                return exc
            return bytes([fc, 2 * x]) + struct.pack(f">{x}H", *vals)
        if fc == 1:
            bits = mod.read_coils(a, x)
            if bits is None:
                return exc
            nbytes = (x + 7) // 8
            return bytes([fc, nbytes]) + bits.to_bytes(nbytes, "little")
        if fc == 5:
            mod.writes.append((5, a, x))
            return pdu[:5] if mod.write_coil(a, x == 0xFF00) else exc
        if fc == 6:
            mod.writes.append((6, a, x))
            return pdu[:5] if mod.write_hr(a, x) else exc
        if fc == 16:
            n = pdu[5] // 2
            vals = struct.unpack(f">{n}H", pdu[6 : 6 + 2 * n])
            for i, v in enumerate(vals):
                mod.writes.append((16, a + i, v))
                if not mod.write_hr(a + i, v):
                    return exc
            return pdu[:5]
        return bytes([fc | 0x80, 1])
