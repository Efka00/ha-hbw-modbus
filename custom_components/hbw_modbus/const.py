"""Konstanten der HBW-Modbus-Integration."""

from datetime import timedelta

DOMAIN = "hbw_modbus"
MANUFACTURER = "HM485MB"

CONF_DEVICE_ADDRESS = "device_address"

DEFAULT_PORT = 502
DEFAULT_TIMEOUT = 3.0
SCAN_INTERVAL = timedelta(seconds=5)

KIND_SWITCH = "sw"  # HBW-MB-Sw (Schaltmodul)
KIND_BLIND = "bl"   # HBW-MB-Bl (Rollladenmodul)
KIND_S0 = "s0"      # HBW-MB-S0 (S0-Impulszähler)
CONF_KIND = "kind"

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

# Register der Schaltmodul-Firmware (siehe README)
HR_CONFIG = 0          # HR0..7: ID, Invertierung, Ausgänge, Flags, Rollladen, Umschalten
HR_OUTPUTS = 2
HR_BLIND_ACT = 4
HR_BLIND_DIR = 5
HR_TOGGLE = 7
HR_TIMER = 20          # HR20..20+N-1: Timer in s
IR_INFO = 0            # IR0..6: Version, Kanäle, HBW-Adresse, Laufzeit, CRC, Speicherzähler
IR_INFO_COUNT = 7
IR_DIAG = 30           # IR30..35 ab HBW-MB-Sw v1.08 / HBW-MB-Bl v1.05
IR_DIAG_COUNT = 6

FW_DIAG_SW = 0x0108
FW_DIAG_BL = 0x0105

RESET_CAUSES = ["unknown", "power_on", "external", "brownout", "watchdog"]

SERVICE_TURN_ON_FOR = "turn_on_for"
ATTR_SECONDS = "seconds"
SERVICE_SET_METER_READING = "set_meter_reading"
ATTR_VALUE = "value"
