"""Ausfälle und Busstörungen eines Moduls sichtbar machen.

* Antwortet ein Modul länger als OFFLINE_ALARM nicht, entsteht eine
  Reparaturmeldung („HBWMB_A11 antwortet nicht“), sobald es wieder antwortet,
  verschwindet sie.
* Steigt der CRC-Fehlerzähler des Moduls innerhalb einer Stunde um mehr als
  CRC_ALARM, entsteht eine zweite Meldung; sie verschwindet, wenn es eine
  Stunde lang ruhiger war.
* Zu jeder Meldung und zur Entwarnung feuert das Ereignis `hbw_modbus_alarm`
  (Daten: module, kind = offline | online | crc | crc_ok, message). Eine
  Automation kann daraus eine Push-Nachricht machen.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util

from .const import DOMAIN

EVENT_ALARM = f"{DOMAIN}_alarm"
OFFLINE_ALARM = timedelta(minutes=2)
CRC_WINDOW = timedelta(hours=1)
CRC_ALARM = 10


def _fmt(t: datetime) -> str:
    return dt_util.as_local(t).strftime("%d.%m. %H:%M")


def module_health(hass: HomeAssistant, entry: ConfigEntry) -> "ModuleHealth":
    """Je Eintrag ein Objekt, das ein Neuladen und Setup-Wiederholungen
    übersteht (sonst würde ein beim Start fehlendes Modul nie gemeldet)."""
    store: dict[str, ModuleHealth] = hass.data.setdefault(f"{DOMAIN}_health", {})
    if entry.entry_id not in store:
        store[entry.entry_id] = ModuleHealth(hass, entry)
    return store[entry.entry_id]


def forget_health(hass: HomeAssistant, entry: ConfigEntry) -> None:
    health = hass.data.get(f"{DOMAIN}_health", {}).pop(entry.entry_id, None)
    if health is not None:
        health.remove()
    else:
        ModuleHealth(hass, entry).remove()


class ModuleHealth:
    """Zustand der Verbindung zu einem Modul, gefüttert vom Coordinator."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.last_seen: datetime | None = None
        self.offline_since: datetime | None = None
        self._offline_reported = False
        self._crc: deque[tuple[datetime, int]] = deque()
        self._crc_reported = False
        self._first = True

    @property
    def _module(self) -> str:
        return self.entry.title

    def _issue_id(self, kind: str) -> str:
        return f"{kind}_{self.entry.entry_id}"

    def _fire(self, kind: str, message: str) -> None:
        self.hass.bus.async_fire(
            EVENT_ALARM, {"module": self._module, "kind": kind, "message": message}
        )

    def _issue(self, kind: str, placeholders: dict[str, str]) -> None:
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self._issue_id(kind),
            is_fixable=False,
            is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=kind,
            translation_placeholders={"module": self._module, **placeholders},
        )

    def success(self, crc_errors: int) -> None:
        now = dt_util.utcnow()
        if self._first:
            # Meldungen aus der Zeit vor dem Neuladen aufräumen
            ir.async_delete_issue(self.hass, DOMAIN, self._issue_id("module_offline"))
            ir.async_delete_issue(self.hass, DOMAIN, self._issue_id("bus_errors"))
            self._first = False
        if self._offline_reported:
            ir.async_delete_issue(self.hass, DOMAIN, self._issue_id("module_offline"))
            self._fire("online", f"{self._module} antwortet wieder (ausgefallen seit {_fmt(self.offline_since)}).")
        self._offline_reported = False
        self.offline_since = None
        self.last_seen = now
        self._check_crc(now, crc_errors)

    def failure(self) -> None:
        now = dt_util.utcnow()
        if self.offline_since is None:
            self.offline_since = self.last_seen or now
        if not self._offline_reported and now - self.offline_since >= OFFLINE_ALARM:
            self._offline_reported = True
            since = _fmt(self.offline_since)
            self._issue("module_offline", {"since": since})
            self._fire("offline", f"{self._module} antwortet seit {since} nicht mehr.")

    def _check_crc(self, now: datetime, value: int) -> None:
        if self._crc and value < self._crc[-1][1]:
            self._crc.clear()   # Zähler nach Neustart des Moduls wieder bei 0
        self._crc.append((now, value))
        while self._crc and now - self._crc[0][0] > CRC_WINDOW:
            self._crc.popleft()
        rise = value - self._crc[0][1]
        if rise > CRC_ALARM and not self._crc_reported:
            self._crc_reported = True
            self._issue("bus_errors", {"count": str(rise)})
            self._fire("crc", f"{self._module}: {rise} gestörte Telegramme in der letzten Stunde (CRC-Fehler).")
        elif rise <= CRC_ALARM and self._crc_reported and now - self._crc[0][0] >= CRC_WINDOW * 0.9:
            self._crc_reported = False
            ir.async_delete_issue(self.hass, DOMAIN, self._issue_id("bus_errors"))
            self._fire("crc_ok", f"{self._module}: Bus wieder ruhig.")

    def remove(self) -> None:
        ir.async_delete_issue(self.hass, DOMAIN, self._issue_id("module_offline"))
        ir.async_delete_issue(self.hass, DOMAIN, self._issue_id("bus_errors"))
