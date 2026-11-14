"""Sicherung der Einstellungen eines Moduls – für den Modultausch.

HA merkt sich je Modul, was im Nano gespeichert ist: Invertierung,
Einschaltverhalten, Rollladen-Kanäle, „Dauer ein“, Fahrzeiten und
Einlernwerte, Notbetrieb, beim Zähler Entprellzeit und Speicherintervall. Die Werte
kommen aus den normalen Abfragen (kein zusätzlicher Busverkehr); gespeichert
wird nur, wenn sich etwas geändert hat.

Fällt ein Modul aus und meldet sich ein frischer Nano unter der Service-ID
247, kann er als Ersatz eingesetzt werden: Die gesicherten Werte und die
alte ID werden ins neue Modul geschrieben, der Eintrag in HA bleibt derselbe
(Entitäten, Namen, Automationen).

Die Zählerstände eines Zählermoduls stehen nicht in dieser Sicherung: Die
schreibt der Koordinator ohnehin zurück, sobald das Modul weniger meldet.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .client import HbwModbusClient, ModbusError
from .const import (
    BLIND_PAR_COUNT,
    DOMAIN,
    FAIL_BLIND,
    HR_BLIND_PAR,
    HR_FAIL_MIN,
    HR_FAIL_OFF,
    HR_FAIL_ON,
    HR_FORCE,
    KIND_BLIND,
    KIND_S0,
    KIND_SWITCH,
)

# Schaltmodul: HR1 Invertierung, HR3 Einschaltverhalten, HR4..6 Rollladen-Kanäle
SW_CONFIG = (1, 3, 4, 5, 6)
# Zähler: HR1 Entprellzeit, HR2 Speicherintervall
S0_CONFIG = (1, 2)


def module_store(hass: HomeAssistant, entry_id: str) -> Store[dict]:
    """Gespeichertes je Modul-Eintrag: Zählerstände und Sicherung."""
    return Store(hass, 1, f"{DOMAIN}.{entry_id}")


def collect(
    kind: str,
    firmware: int,
    channels: int,
    config: list[int] | None,
    blind_par: list[list[int]],
    s0_config: list[int] | None,
    has_force: bool,
    fail: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Sicherung aus den zuletzt gelesenen Werten; None, solange etwas fehlt.
    fail: Notbetrieb (min, on, off, blind), wenn die Firmware ihn kann."""
    regs: dict[int, int] = {}
    if kind == KIND_SWITCH:
        if config is None:
            return None
        regs.update({r: config[r] for r in SW_CONFIG})
        if has_force:
            regs[HR_FORCE] = config[HR_FORCE]
        if blind_par:
            regs.update({10 + HR_BLIND_PAR + i: v for i, v in enumerate(blind_par[0])})
    elif kind == KIND_BLIND:
        if len(blind_par) != channels:
            return None
        for n, par in enumerate(blind_par, 1):
            regs.update({10 * n + HR_BLIND_PAR + i: v for i, v in enumerate(par[:BLIND_PAR_COUNT])})
    elif kind == KIND_S0:
        if s0_config is None:
            return None
        regs.update(dict(zip(S0_CONFIG, s0_config)))
    if fail is not None:
        regs[HR_FAIL_MIN] = fail["min"]
        regs.update({10 * n + FAIL_BLIND: v for n, v in enumerate(fail["blind"], 1)})
        if kind == KIND_SWITCH:
            regs[HR_FAIL_ON], regs[HR_FAIL_OFF] = fail["on"], fail["off"]
    return {
        "kind": kind,
        "channels": channels,
        "firmware": firmware,
        "registers": {str(r): v for r, v in sorted(regs.items())},
    }


def same(a: dict[str, Any] | None, b: dict[str, Any] | None) -> bool:
    keys = ("kind", "channels", "firmware", "registers")
    return a is not None and b is not None and all(a.get(k) == b.get(k) for k in keys)


def stamped(backup: dict[str, Any]) -> dict[str, Any]:
    return {**backup, "changed": dt_util.utcnow().isoformat()}


async def restore(client: HbwModbusClient, unit: int, backup: dict[str, Any]) -> list[int]:
    """Gesicherte Werte ins Modul schreiben und zurücklesen. Reihenfolge nach
    Register: beim Schaltmodul erst die Rollladen-Kanäle (HR4/5), dann dessen
    Fahrzeiten (HR12..). Rückgabe: Register, die nicht stimmen."""
    failed: list[int] = []
    for reg_s, value in sorted(backup["registers"].items(), key=lambda kv: int(kv[0])):
        reg = int(reg_s)
        try:
            await client.write_register(unit, reg, value, retry=True)
            if (await client.read_registers(unit, False, reg, 1))[0] != value:
                failed.append(reg)
        except ModbusError:
            failed.append(reg)
    return failed
