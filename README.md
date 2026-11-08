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

## Module hinzufügen

### Automatisch über den Bus (empfohlen)

Ist ein Modul eingerichtet, bietet HA den **Bus** des Gateways als
„Gefunden“ an (oder von Hand: Integration hinzufügen → „HBW Modbus“ →
„Bus hinzufügen“). Das Bus-Gerät `HBW-Bus 192.168.1.42` hat zwei Knöpfe:

| Knopf | Was passiert |
|---|---|
| Neues Modul suchen | eine Anfrage an die Service-ID 247 – dort meldet sich ein frisch geflashter Nano |
| Bus durchsuchen | die IDs 1–19, 21–29 und 247 außer den schon eingerichteten, je höchstens 0,3 s (zusammen höchstens etwa 10 s) |

Von sich aus fragt der Bus nichts ab: Ein Modbus-Modul antwortet nur, wenn
es gefragt wird, und der Bus soll schlank bleiben. Das Ergebnis der Suche
steht in einer Benachrichtigung.

Gefundene Module erscheinen unter Einstellungen → Geräte & Dienste als
„Gefunden“ und werden erst nach **Rückfrage** eingerichtet:

- Modul mit eigener ID (z. B. ein umgeflashtes HBW-Modul, das seine
  A-Nummer übernommen hat): bestätigen, fertig.
- Modul unter ID 247: Die Rückfrage schlägt die nächste freie ID vor
  (Schalt- und Rollladenmodule 1–19, Zähler 21–29). Vor dem Schreiben
  prüft die Integration, dass am Bus niemand unter der neuen ID antwortet;
  danach, dass das Modul unter ihr antwortet.

**Vergabe der IDs:** 1–19 für Schalt- und Rollladenmodule (A-Nummer =
ID, `HBWMB_A01` …), 21–29 für Zähler (`HBWMB_S01` = ID 21 …). Die ID 0 ist
der Broadcast an alle Module.

Neue Nanos nacheinander anschließen: Zwei Module unter ID 247 antworten
gleichzeitig und stören sich.

### Von Hand

Integration hinzufügen → „HBW Modbus“ → „Modul hinzufügen“:

| Feld | Inhalt |
|---|---|
| Gateway | IP oder Name des Modbus-TCP-Gateways |
| Port | meist 502 |
| Modbus-ID | ID des Moduls (1–247) |
| Name | optional, Name des Modulgeräts (leer = `HBWMB_A` + Modbus-ID, z. B. `HBWMB_A03`; Zählermodule ab ID 21 = `HBWMB_S01`) |

Die Integration liest Firmware-Version, Kanalzahl und Modultyp aus dem
Modul. Je Modul entsteht ein Eintrag, alle Module an einem Gateway teilen
sich eine Verbindung.

## Schaltmodul (HBW-MB-Sw)

Aufgebaut wie die bisherigen HM485-Geräte:

| Gerät | Geräte-Informationen | Entitäten |
|---|---|---|
| Modul, Name `HBWMB_A03` (A + Modbus-ID) | „HBW-MB-Sw (12 Kanäle) von HM485MB“, Firmware-Version | Diagnose: Laufzeit, CRC-Fehler; ab Firmware v1.08 zusätzlich Neustart-Grund, Watchdog-Neustarts, Starts, Betriebsspannung |
| je Kanal, Name `HBWMB_A03 Kanal n` | „HBWMB_A03 von HM485MB“, verbunden über das Modul | Schalter. `switch.toggle` schaltet im Modul selbst um. Attribut `timer_remaining`: Restzeit des Ausschalt-Timers in s |

Die Kanalgeräte lassen sich wie gewohnt umbenennen, z. B. in
„EG_AK_BL_Decke“.

**Rollladen auf zwei Kanälen:** Ist am Modul ein Rollladen eingerichtet
(HR4 Motor, HR5 Richtung, siehe Firmware), gibt es für diese beiden Kanäle
kein Kanalgerät. Stattdessen erscheint das Gerät `HBWMB_A03 Rollladen` mit
einem Rollladen (Auf, Ab, Stopp, Position). Fahrlogik, Kennlinie und
Schutzpausen laufen im Modul. Solange die Position nach dem Einrichten oder
einem Neustart während der Fahrt unbekannt ist, zeigt HA keine Position an;
die erste Fahrt in eine Endlage macht sie wieder bekannt. Während einer
Fahrt fragt die Integration jede Sekunde ab. Rollladenmodule (HBW-MB-Bl)
kann die Integration noch nicht.

Am Rollladengerät stehen unter „Konfiguration“ die Einstellungen, die das
Modul speichert:

| Einstellung | Bereich | Bedeutung |
|---|---|---|
| Fahrzeit auf / zu | 1–600 s | gesamte Fahrzeit ab Motorstart |
| Zeit bis Endleiste hebt ab | 0–600 s | Schlitzphase beim Auffahren, 0 = keine |
| Zeit bis Endleiste in der Mitte | 0–600 s | Kennlinie (Wickelradius), 0 = linear |
| Totzeit beim Anlauf | 0–5 s | Zeit ohne Bewegung nach Motorstart |
| Endlagenzuschlag | 0–100 % | Nachlauf an den Endlagen, Standard 10 % |
| Referenzfahrt nach Zwischenfahrten | 0–1000 | nach so vielen Fahrten ohne Endlage erst in eine Endlage, 0 = aus |

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

## Zählermodul (HBW-MB-S0)

| Gerät | Geräte-Informationen | Entitäten |
|---|---|---|
| Modul, Name `HBWMB_S01` (Modbus-ID 21 = S01) | „HBW-MB-S0 (8 Eingänge) von HM485MB“ | Diagnose wie beim Schaltmodul |
| je Eingang, Name `HBWMB_S01 Eingang n` | „HBWMB_S01 von HM485MB“ | „Zählerstand“ in kWh (für das Energie-Dashboard, Attribut `pulses` = Impulse), „Leistung“ in W |

- **Impulse pro kWh:** Standard 1000, einstellbar unter „Konfigurieren“ am
  Eintrag (gilt für alle Eingänge des Moduls).
- **Leistung:** aus dem Abstand der letzten beiden Impulse. Kommen keine
  Impulse mehr, sinkt sie mit der Zeit seit dem letzten Impuls ab.
- **Zählerstand angleichen:**

  ```yaml
  action: hbw_modbus.set_meter_reading
  target:
    entity_id: sensor.hbwmb_s01_eingang_1_zahlerstand
  data:
    value: 12345.678
  ```

- **Schutz vor Rücksprüngen:** Das Modul speichert seine Zählerstände nur
  alle 10 Minuten. Fällt der Strom aus, meldet es danach etwas weniger. Die
  Integration merkt sich den höchsten Stand je Eingang und schreibt ihn
  dann ins Modul zurück, damit das Energie-Dashboard keinen Sprung macht.
  Einen Zählerstand nach unten setzen deshalb nur über
  `hbw_modbus.set_meter_reading`, nicht über die USB-Konsole des Moduls.

## Betrieb

- Jedes Modul wird alle 5 s abgefragt (drei bis vier Anfragen).
- Antwortet ein Modul nicht, sind seine Entitäten „nicht verfügbar“, bis es
  wieder antwortet.
- Nach einem Firmware-Update, einem Modultausch unter derselben ID oder
  wenn am Modul ein Rollladen ein- oder ausgerichtet wird, lädt sich der
  Eintrag selbst neu. Gerät und Entitäten passen dann zum neuen Stand.
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
