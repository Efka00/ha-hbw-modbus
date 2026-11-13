"""Wickelkennlinie eines Rollladens, gerechnet wie in der Firmware.

1:1 übertragen aus HBW-MB-Bl / HBW-MB-Sw (recalc, pctToU, uToPct, travelMs,
endExtra, livePos). Damit kann HA die Position während einer Fahrt selbst
fortschreiben und weiß, wann der Rollladen ankommt – statt das Modul jede
Sekunde abzufragen.

Einheiten wie im Modul: Position 0..10000 (0,01 %), Zeiten in ms,
Parameter roh aus HR10n+2..8 (Fahrzeiten in 10 ms, Zuschlag in %).
Ändert sich die Kennlinie in der Firmware, muss sie hier mit – ein Test
vergleicht beide (test/blindcalc_vs_firmware).
"""

from __future__ import annotations

import math

POS_MAX = 10000
POS_SLOT = 1000   # 10 % = Lüftungsschlitze ganz offen

P_TUP, P_TDN, P_TLIFT, P_TMID, P_TDEAD, P_SURCH, P_REFN = range(7)


class BlindCurve:
    """Kennlinie eines Rollladens aus seinen Parametern (recalc() der Firmware)."""

    def __init__(self, par: list[int]) -> None:
        self.par = list(par)
        up, dn = par[P_TUP] * 10.0, par[P_TDN] * 10.0
        dead = par[P_TDEAD] * 10.0
        lim = min(up, dn) - 100
        if dead > lim:
            dead = lim if lim > 0 else 0
        self.dead = dead
        move = up - dead
        self.scale = (dn - dead) / move   # abwärts: gleiche Kennlinie, andere Geschwindigkeit
        lift = par[P_TLIFT] * 10.0 - dead
        if par[P_TLIFT] == 0 or lift <= 0:
            self.p_slot, self.ls = 0, 0.0
        else:
            self.p_slot, self.ls = POS_SLOT, min(lift, move * 0.5)
        self.body = move - self.ls
        c = (5000 - POS_SLOT) / (POS_MAX - POS_SLOT) if self.p_slot else 0.5
        tm = (par[P_TMID] * 10.0 - dead - self.ls) / self.body
        self.k = 0.0
        if par[P_TMID] and 0.05 < tm < 0.95:
            den = tm * tm - c
            self.k = (c - tm) / den if den < -1e-4 else 50.0
            self.k = min(self.k, 50.0)
            self.k = max(self.k, -0.45)   # Kennlinie bleibt steigend

    def pct_to_u(self, p: float) -> float:
        """Position -> Fahrzeit ab ganz zu (ms, ohne Totzeit, aufwärts)."""
        if p <= self.p_slot:
            return self.ls * p / self.p_slot if self.p_slot else 0.0
        h = (p - self.p_slot) / (POS_MAX - self.p_slot)
        if abs(self.k) < 1e-4:
            t = h
        else:
            t = (-1 + math.sqrt(1 + 4 * self.k * (1 + self.k) * h)) / (2 * self.k)
        return self.ls + t * self.body

    def u_to_pct(self, u: float) -> int:
        if u <= 0:
            return 0
        if u < self.ls:
            return int(self.p_slot * u / self.ls + 0.5)
        t = (u - self.ls) / self.body
        if t >= 1:
            return POS_MAX
        h = (t + self.k * t * t) / (1 + self.k)
        return self.p_slot + int(h * (POS_MAX - self.p_slot) + 0.5)

    def travel_ms(self, frm: int, to: int) -> float:
        """Fahrzeit ohne Totzeit von frm nach to."""
        u0, u1 = self.pct_to_u(frm), self.pct_to_u(to)
        return u1 - u0 if to > frm else (u0 - u1) * self.scale

    def end_extra(self, seg: int, up: bool) -> float:
        """Endlagenzuschlag in ms, nur wenn eine Endlage angefahren wird."""
        if seg not in (0, POS_MAX):
            return 0.0
        return (self.par[P_TUP] if up else self.par[P_TDN]) * 10.0 * self.par[P_SURCH] / 100

    def position(self, start: int, up: bool, elapsed_ms: float) -> int:
        """Position nach elapsed_ms Fahrt ab start (livePos ohne Totzeit)."""
        if elapsed_ms <= 0:
            return start
        u0 = self.pct_to_u(start)
        return self.u_to_pct(u0 + elapsed_ms if up else u0 - elapsed_ms / self.scale)
