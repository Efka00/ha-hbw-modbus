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
es gefragt wird, und der Bus soll schlank bleiben. Der Sensor **Suche** am
Bus-Gerät zeigt, was gerade passiert: „Läuft“ (Attribut Fortschritt, z. B.
12/29), danach „Nichts gefunden“, „Neues Modul gefunden“ oder „Gefunden,
nicht einrichtbar“ (es hat sich etwas gemeldet, das HA nicht einrichten
kann: ein in HA ignoriertes Modul, ein fremdes Modbus-Gerät oder eine
unvollständige Antwort); das Ergebnis steht im Attribut „Ergebnis“ und
zusätzlich in einer Benachrichtigung.

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
| Modul, Name `HBWMB_A03` (A + Modbus-ID) | „HBW-MB-Sw (12 Kanäle) von HM485MB“, Firmware-Version | Diagnose: Laufzeit, CRC-Fehler; ab Firmware v1.08 zusätzlich Neustart-Grund, Watchdog-Neustarts, Starts, Betriebsspannung; ab v1.11 Busfehlerquote, verlorene Anfragen/Antworten |
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
Fahrt rechnet die Integration die Position selbst fort – mit derselben
Wickelkennlinie wie das Modul (`blindcalc.py`, gegen den Firmware-Code
getestet) – und fragt das Modul erst bei der berechneten Ankunft wieder ab.
Das spart pro Fahrt rund 90 % der Busanfragen, wichtig wenn viele Rollläden
gleichzeitig fahren. Die Endposition kommt immer aus dem Modul; unterwegs
kann die Anzeige um 1–2 % abweichen (Relaispausen vor dem Anlauf). Nur
während einer Einlernfahrt fragt sie jede Sekunde ab. Positionen: 0 % = zu, 10 % = Lüftungsschlitze
ganz offen (ab Firmware HBW-MB-Sw v1.09 / HBW-MB-Bl v1.06, vorher 1 %),
50 % = Endleiste in der Fenstermitte, 100 % = auf. Dafür muss die „Zeit
bis Endleiste hebt ab“ eingestellt sein.

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

### Typ je Kanal: Schalter oder Licht

Am Kanalgerät steht unter „Konfiguration“ die Auswahl **Typ**:
„Schalter“ (Standard) legt `switch.…` an, „Licht“ ein einfaches Licht
(ein/aus) `light.…`. Name, Gerät und Funktionen (Timer-Dienst, Dauer ein)
bleiben gleich, nur die Art der Entität wechselt; der Eintrag lädt sich
dafür kurz neu. Die Entitäts-ID ergibt sich aus dem Gerätenamen, z. B.
Gerät „EG_WZ1_LED1“ → `light.eg_wz1_led1`.

### Dauer ein (ab Firmware HBW-MB-Sw v1.10)

Für Leuchten, hinter denen eine Zigbee-Lampe sitzt und die deshalb immer
Strom brauchen: Am Kanalgerät steht unter „Konfiguration“ der Schalter
**Dauer ein**.

- Eingeschaltet geht der Kanal sofort an. Das Modul hält ihn selbst an,
  auch nach einem Stromausfall und unabhängig von Home Assistant.
- Ausschalten, Umschalten und Timer auf diesem Kanal ignoriert die
  Integration still (ab v0.12.1, vorher mit Fehlermeldung), der Kanal
  bleibt an. So laufen Automationen, Szenen und Assist („alle Lichter im
  Raum aus“) ohne Fehler weiter, nur dieser Kanal bleibt an. Das Modul
  selbst würde einen Ausschaltbefehl ebenfalls ablehnen.
- Wieder ausgeschaltet bleibt der Kanal zunächst an und lässt sich dann
  normal schalten.
- Das Attribut `always_on` am Kanalschalter zeigt die Sperre an.

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

### Einlernfahrt (Rollladen)

Am Rollladengerät stehen unter „Konfiguration“ die Knöpfe **Einlernfahrt
starten**, **Marker setzen** und **Einlernfahrt abbrechen**. Der Sensor
**Einlernen** zeigt jederzeit, was gerade passiert und welcher Marker als
Nächstes kommt:

1. „Einlernfahrt starten“: Der Rollladen fährt ganz zu (Sensor: „Fährt zu
   (warten)“) und danach auf.
2. „Marker setzen“, wenn die Endleiste abhebt, wenn sie in der Fenstermitte
   steht und wenn der Rollladen oben ankommt.
3. Er fährt wieder zu: „Marker setzen“, wenn er unten ankommt.
4. Sensor „Fertig, Zeiten gespeichert“ – Fahrzeiten, „Zeit bis Endleiste
   hebt ab“ und „Mitte“ stehen jetzt in den Einstellungen.

Jeder andere Befehl bricht die Einlernfahrt ab, ebenso 5 Minuten ohne
Marker.

## Rollladenmodul (HBW-MB-Bl)

Je Modul vier Rollläden, jeder als eigenes Gerät:

| Gerät | Geräte-Informationen | Entitäten |
|---|---|---|
| Modul, Name `HBWMB_A04` (A + Modbus-ID) | „HBW-MB-Bl (4 Rollläden) von HM485MB“, Firmware-Version | Diagnose wie beim Schaltmodul (Neustart-Grund usw. ab Firmware v1.05) |
| je Rollladen, Name `HBWMB_A04 Rollladen n` | „HBWMB_A04 von HM485MB“, verbunden über das Modul | Rollladen (Auf, Ab, Stopp, Position) und die Einstellungen aus der Tabelle oben |

Verhalten, Positionen und Einstellungen sind dieselben wie beim Rollladen
auf dem Schaltmodul. Je Abfrage liest die Integration alle vier Rollläden
mit einer Anfrage; die Einstellungen liest sie nur beim Start und nach
einer Einlernfahrt, Änderungen aus HA übernimmt sie direkt.

## Zählermodul (HBW-MB-S0)

| Gerät | Geräte-Informationen | Entitäten |
|---|---|---|
| Modul, Name `HBWMB_S01` (Modbus-ID 21 = S01) | „HBW-MB-S0 (8 Eingänge) von HM485MB“ | Diagnose wie beim Schaltmodul |
| je Eingang, Name `HBWMB_S01 Eingang n` | „HBWMB_S01 von HM485MB“ | „Zählerstand“ in kWh (für das Energie-Dashboard, Attribut `pulses` = Impulse), „Leistung“ in W |

- **Impulse pro kWh:** Standard 1000, einstellbar unter „Konfigurieren“ am
  Eintrag (gilt für alle Eingänge des Moduls).
- **Leistung:** aus dem Abstand der letzten beiden Impulse. Kommen keine
  Impulse mehr, sinkt sie mit der Zeit seit dem letzten Impuls ab.
- **Mindestleistung:** Standard 10 W, einstellbar unter „Konfigurieren“
  (0 = aus). Leistungen darunter zeigt HA als 0 W. Mit 1 Wh pro Impuls ist
  kleine Leistung ohnehin erst nach vielen Minuten erkennbar; so fällt die
  Anzeige nach dem Abschalten in absehbarer Zeit auf 0 (bei 10 W und 1000
  Impulsen/kWh 6 Minuten nach dem letzten Impuls). Beispiel Waschmaschine:
  „fertig“, wenn die Leistung 2 Minuten lang 0 W ist. Der Zählerstand zählt
  immer alles, auch Standby unter der Mindestleistung.
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
  wieder antwortet. Bis dahin fragt die Integration es nur noch einmal pro
  Minute ab, mit einer einzigen Anfrage und 1 s Wartezeit. So blockiert ein
  ausgefallenes oder abgeklemmtes Modul die gemeinsame Verbindung nicht für
  die anderen Module (Schaltbefehle, Suche). Antwortet es wieder, gilt
  sofort wieder der 5-s-Takt.
- Befehle haben Vorrang vor Abfragen: Schalten, Rollladen fahren und
  Einstellungen schreiben laufen vor allen wartenden Abfragen. Wartet eine
  Abfrage schon länger als 0,2 s auf ihre Antwort (z. B. von einem Modul,
  das nicht antwortet), gibt sie die Verbindung frei und wird nach dem
  Befehl neu gestellt. Eine normale Antwort (rund 30 ms) läuft vorher
  ungestört durch, damit der Befehl nicht in eine laufende Antwort am Bus
  fällt.
- Befehle warten 0,5 s auf die Quittung des Moduls. Bleibt sie aus, etwa
  weil das Telegramm am Bus gestört wurde, wird der Befehl einmal
  wiederholt (1 s Wartezeit) und im Log als Warnung „zweiter Versuch“
  vermerkt. Erst wenn auch das scheitert, meldet HA einen Fehler – nach
  höchstens rund 1,5 s. Wiederholt werden nur Befehle, die doppelt
  ausgeführt dasselbe Ergebnis haben: Ein/Aus, Timer, „Dauer ein“,
  Rollladen auf/zu/Stopp/Position, Parameter und Zählerstände. Nicht
  wiederholt werden Umschalten, Einlernen starten, Einlern-Marker und das
  Ändern der Modul-ID.
- Nach einem Firmware-Update, einem Modultausch unter derselben ID oder
  wenn am Modul ein Rollladen ein- oder ausgerichtet wird, lädt sich der
  Eintrag selbst neu. Gerät und Entitäten passen dann zum neuen Stand.
- Der Waveshare schickt die Antworten vom Bus an alle verbundenen
  TCP-Clients. Die Integration verwirft fremde Antworten, andere Programme
  am selben Gateway (z. B. die Modbus-YAML von Home Assistant) melden dann
  aber Fehler. Am besten greift nur diese Integration auf das Gateway zu.

## Ausfälle und Störungen

- Am Modulgerät zeigt der Diagnosesensor **Verbindung** (verbunden /
  getrennt), ob das Modul antwortet. Er bleibt auch bei einem Ausfall
  sichtbar und hat die Attribute `last_seen` und `offline_since`.
- Antwortet ein Modul länger als **2 Minuten** nicht – auch wenn es schon
  beim Start von Home Assistant fehlt –, erscheint unter Einstellungen →
  Reparaturen „HBWMB_A11 antwortet nicht“. Die Meldung verschwindet, sobald
  es wieder antwortet.
- Steigt der CRC-Fehlerzähler eines Moduls innerhalb einer Stunde um mehr
  als **10**, erscheint „Busstörungen bei …“. Sie verschwindet nach einer
  ruhigen Stunde.
- Ab Firmware HBW-MB-Sw v1.11 (Bl v1.07, S0 v1.03) zählt das Modul die
  Rahmen am Bus mit (IR36..38). Dazu kommen drei Diagnosesensoren:
  - **Busfehlerquote**: Anteil gestörter Rahmen (CRC-Fehler, zu kurz/zu
    lang) an allen Rahmen, die das Modul in der letzten Stunde gesehen hat.
    Bei einem gesunden Bus nahe 0 %.
  - **Verlorene Anfragen**: Anfragen und Befehle von HA, die das Modul nie
    verstanden hat (Hinweg: Gateway, Leitung, Störung). HA vergleicht dazu
    seine gesendeten Anfragen mit dem Anfragezähler des Moduls.
  - **Verlorene Antworten**: Das Modul hat die Anfrage verstanden, die
    Antwort kam aber nicht bei HA an (Rückweg).

  Beide Verlustzähler beginnen beim Start von HA bei 0. Zeiten, in denen
  das Modul offline war oder neu gestartet ist, zählen nicht mit. Jeder
  Verlust steht außerdem als Info-Meldung im Log. Ein verlorener Befehl
  wird seit v0.11.1 automatisch wiederholt; die Zähler zeigen, wie oft das
  nötig war und auf welchem Weg es hakt.
- Zu jeder Meldung und Entwarnung feuert das Ereignis `hbw_modbus_alarm`
  mit `module`, `kind` (`offline`, `online`, `crc`, `crc_ok`) und
  `message`. Eine Automation kann daraus eine Push-Nachricht machen:

```yaml
triggers:
  - trigger: event
    event_type: hbw_modbus_alarm
actions:
  - action: notify.mobile_app_mein_handy
    data:
      title: HBW-Bus
      message: "{{ trigger.event.data.message }}"
```

## Tests

```bash
uv venv .venv && uv pip install pytest-homeassistant-custom-component
.venv/bin/pytest tests
```

Die Tests laufen gegen ein nachgebautes Gateway mit Schaltmodul.
