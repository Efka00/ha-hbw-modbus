# HBW Modbus für Home Assistant

Integration für Homematic-Wired-Homebrew-Module (HBW), die mit der
Modbus-RTU-Firmware **HBW-MB** laufen (Arduino Nano mit MAX485 am
RS485-Bus). Die Module hängen über ein Modbus-TCP-Gateway im Netz, z. B.
einen Waveshare RS485 TO ETH im Modus „Modbus TCP to RTU“.

Jedes Modul wird ein eigenes Gerät in Home Assistant. Kein YAML, kein
MQTT-Broker, keine weitere Software.

## Installation

Über den Marketplace (früher HACS): Marketplace → ⋮ → Benutzerdefinierte
Repositories → `Efka00/ha-hbw-modbus`, Typ „Integration“. Dann „HBW
Modbus“ herunterladen und Home Assistant neu starten.

## Modul hinzufügen

Einstellungen → Geräte & Dienste → Integration hinzufügen → „HBW Modbus“:

| Feld | Inhalt |
|---|---|
| Gateway | IP oder Name des Modbus-TCP-Gateways |
| Port | meist 502 |
| Modbus-ID | ID des Moduls (1–247) |
| Name | optional, Name des Geräts |

Die Integration liest Firmware-Version, Kanalzahl und Modultyp aus dem
Modul. Je Modul entsteht ein Eintrag, alle Module an einem Gateway teilen
sich eine Verbindung.

## Entitäten (Schaltmodul HBW-MB-Sw)

| Entität | Inhalt |
|---|---|
| Schalter „Kanal n“ | Relais schalten. `switch.toggle` schaltet im Modul selbst um |
| Sensor „Timer Kanal n“ | Restzeit des Ausschalt-Timers in s (0 = kein Timer) |
| Diagnose | Laufzeit, CRC-Fehler; ab Firmware v1.08 zusätzlich Neustart-Grund, Watchdog-Neustarts, Starts, Betriebsspannung |

Die beiden Kanäle eines am Modul eingerichteten Rollladens werden nicht als
Schalter angelegt. Rollladenmodule (HBW-MB-Bl) und Rollläden auf
Schaltmodulen kann die Integration noch nicht.

### Einschalten mit Timer

Wie `on-for-timer` in FHEM: Das Modul schaltet den Kanal nach der Zeit
selbst wieder aus, auch ohne Verbindung zu Home Assistant.

```yaml
action: hbw_modbus.turn_on_for
target:
  entity_id: switch.og_flur_kanal_3
data:
  seconds: 300
```

## Betrieb

- Jedes Modul wird alle 5 s abgefragt (drei bis vier Anfragen).
- Antwortet ein Modul nicht, sind seine Entitäten „nicht verfügbar“, bis es
  wieder antwortet.
- Der Waveshare schickt die Antworten vom Bus an alle verbundenen
  TCP-Clients. Die Integration verwirft fremde Antworten, andere Programme
  am selben Gateway (z. B. die Modbus-YAML von Home Assistant) melden dann
  aber Fehler. Am besten greift nur diese Integration auf das Gateway zu.

## Tests

```bash
uv venv .venv && uv pip install pytest-homeassistant-custom-component
.venv/bin/pytest tests
```

Die Tests laufen gegen ein nachgebautes Gateway mit Schaltmodul.
