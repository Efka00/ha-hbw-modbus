"""Kennlinie in blindcalc.py gegen die Firmware.

blind_curve_reference.json erzeugt das Firmware-Repo (hbw-modbus, test/kurve.cpp)
aus dem echten Code von HBW-MB-Bl. Ändert sich die Kennlinie dort, Tabelle neu
erzeugen – dieser Test zeigt dann, ob blindcalc.py mitgezogen werden muss.
"""

import json
from pathlib import Path

from custom_components.hbw_modbus.blindcalc import BlindCurve

REF = json.loads((Path(__file__).parent / "blind_curve_reference.json").read_text())


def test_curve_matches_firmware() -> None:
    for s in REF:
        c = BlindCurve(s["par"])
        assert abs(c.k - s["k"]) < 1e-4 and abs(c.scale - s["scale"]) < 1e-4, s["par"]
        for p, u in s["p2u"]:
            assert abs(c.pct_to_u(p) - u) <= 1.0, (s["par"], p)
        for u, p in s["u2p"]:
            assert abs(c.u_to_pct(u) - p) <= 1, (s["par"], u)   # Rundung float/double
        for f, t, ms in s["travel"]:
            assert abs(int(c.travel_ms(f, t)) - ms) <= 1, (s["par"], f, t)
        for seg, up, ms in s["extra"]:
            assert int(c.end_extra(seg, bool(up))) == ms, (s["par"], seg, up)
