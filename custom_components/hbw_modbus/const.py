"""Konstanten der HBW-Modbus-Integration."""

from datetime import timedelta

DOMAIN = "hbw_modbus"
MANUFACTURER = "HM485MB"

CONF_DEVICE_ADDRESS = "device_address"

DEFAULT_PORT = 502
DEFAULT_TIMEOUT = 3.0
SCAN_INTERVAL = timedelta(seconds=5)
# Antwortet ein Modul nicht, wird es nur noch selten und kurz abgefragt
# (eine Anfrage, 1 s Wartezeit), damit es die gemeinsame Verbindung zum
# Gateway nicht für die anderen Module blockiert.
OFFLINE_INTERVAL = timedelta(seconds=60)
OFFLINE_TIMEOUT = 1.0

KIND_SWITCH = "sw"  # HBW-MB-Sw (Schaltmodul)
KIND_BLIND = "bl"   # HBW-MB-Bl (Rollladenmodul)
KIND_S0 = "s0"      # HBW-MB-S0 (S0-Impulszähler)
KIND_BUS = "bus"    # kein Modul: der Bus am Gateway (Suche nach neuen Modulen)
CONF_KIND = "kind"

# Suche nach Modulen: neue Nanos melden sich unter der Service-ID 247.
# Bei freien IDs wartet der Waveshare selbst ~200 ms, bevor er die nächste
# Anfrage auf den Bus gibt.
SERVICE_ID = 247
PROBE_TIMEOUT = 0.3
SEARCH_TIMEOUT = 1.0
# Vergebene IDs (0 ist der Broadcast): A = Ausgänge (Schalt- und
# Rollladenmodule) 1–19, S = Zähler 21–29 (21 = S01). Nur diese IDs und die
# Service-ID werden bei „Bus durchsuchen“ abgefragt.
ID_RANGES = {
    KIND_SWITCH: range(1, 20),
    KIND_BLIND: range(1, 20),
    KIND_S0: range(21, 30),
}
SCAN_IDS = [*range(1, 20), *range(21, 30), SERVICE_ID]

# IR7 = Modultyp (ab HBW-MB-S0; ältere Firmware kennt IR7 nicht)
IR_MODULE_TYPE = 7
MODULE_TYPE_S0 = 3

# Register des Zählermoduls
IR_COUNTS = 10         # IR10..25: Zählerstand je Eingang, 32 Bit
HR_COUNTS = 10         # HR10..25: Zählerstand setzen
IR_PULSE = 40          # IR40..55 Impulsabstand ms, IR56..71 seit letztem Impuls ms, je 32 Bit
NO_PULSE = 0xFFFFFFFF
CONF_PULSES_PER_KWH = "pulses_per_kwh"
DEFAULT_PULSES_PER_KWH = 1000
# Leistungen darunter zeigt die Integration als 0 W. Mit 1 Wh pro Impuls ist
# kleine Leistung ohnehin erst nach vielen Minuten erkennbar; so fällt die
# Anzeige nach dem Abschalten in absehbarer Zeit auf 0 (10 W: nach 6 min).
CONF_MIN_POWER = "min_power"
DEFAULT_MIN_POWER = 10

# Register der Schaltmodul-Firmware (siehe README)
HR_CONFIG = 0          # HR0..7: ID, Invertierung, Ausgänge, Flags, Rollladen, Umschalten
HR_OUTPUTS = 2
HR_BLIND_ACT = 4
HR_BLIND_DIR = 5
HR_TOGGLE = 7
HR_FORCE = 8           # Dauer ein, Bit n = Kanal n+1 (ab HBW-MB-Sw v1.10)
FW_FORCE_SW = 0x0110
HR_TIMER = 20          # HR20..20+N-1: Timer in s
IR_INFO = 0            # IR0..6: Version, Kanäle, HBW-Adresse, Laufzeit, CRC, Speicherzähler
IR_INFO_COUNT = 7
IR_INFO_COUNT_BL = 6   # HBW-MB-Bl hat kein IR6
IR_DIAG = 30           # IR30..35 ab HBW-MB-Sw v1.08 / HBW-MB-Bl v1.05
IR_DIAG_COUNT = 6

# Rollladen n (1..4 beim HBW-MB-Bl, nur 1 auf dem Schaltmodul): Register ab 10·n
HR_BLIND_CMD = 0       # +0 schreiben: 0 zu, 1 auf, 2 Stopp; lesen: Status
HR_BLIND_POS = 1       # +1 schreiben: Position 0..100 anfahren
HR_BLIND_PAR = 2       # +2..+8: Fahrzeiten und Kennlinie (gespeichert)
BLIND_PAR_COUNT = 7
# IR10..: je Wert ein Block mit 4 Einträgen (Rollladen 1..4): Position,
# Status, bekannt, Einlernschritt, Zwischenfahrten. Das Schaltmodul hat nur
# Rollladen 1, die Abstände sind gleich (IR10, 14, 18, 22, 26).
IR_BLIND = 10
BLIND_STRIDE = 4
BLIND_VALUES = 5
BLIND_CLOSED, BLIND_OPEN, BLIND_OPENING, BLIND_CLOSING = 0, 1, 2, 3
SCAN_INTERVAL_MOVING = timedelta(seconds=1)

FW_DIAG_SW = 0x0108
FW_DIAG_BL = 0x0105

RESET_CAUSES = ["unknown", "power_on", "external", "brownout", "watchdog"]

SERVICE_TURN_ON_FOR = "turn_on_for"
ATTR_SECONDS = "seconds"
SERVICE_SET_METER_READING = "set_meter_reading"
ATTR_VALUE = "value"
