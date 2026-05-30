# dSS REST-API Notes

Verifizierte API-Eigenheiten der dSS v1.54.0.

**Status:** Live-verifiziert durch Discovery-Run 2026-05-28 gegen die Box `<dss-host>` (<dss-host>:8080). Schemas + Pfade entsprechen tatsaechlichem Verhalten der Box, nicht (mehr) den Reference-Implementierungen.

Quellen:
- Discovery-Run 2026-05-28: 110 echte Events, 274 Devices, 17 System-Addons gedumpt
- Roh-Daten in `archive/`: `apartment_structure.json`, `property_tree.json`, `events_captured.json`, `scripts_addons.json`
- ioBroker-Reference-Scripts (Wecker, Aussensteckdosen, Auth) - reale Working-Examples
- ioBroker.digitalstrom Adapter + Mat931 HA-Integration (nur als historischer Kontext)

## Connection-Basics

- **Protokoll:** HTTPS (TCP/8080), self-signed Certificate
- **Cert-Verification:** ueberspringen (`verify=False`) oder Fingerprint pinnen
- **Content-Type:** JSON in/out (`application/json`)
- **Endpoint-Praefix:** `/json/...`
- **JSON-Response-Shape:** `{"result": <data>, "ok": true}` bei Erfolg, `{"ok": false, "message": "..."}` bei Fehler

## Authentication-Endpoints

### App-Token erstellen (einmalig per Hand)

```
GET /json/system/requestApplicationToken?applicationName=<NAME>
→ {"result":{"applicationToken":"<TOKEN>"},"ok":true}
```

Anschliessend im dSS-Web-UI unter `Settings → System Access → Authorized Applications` **approven**, sonst nicht nutzbar.

**Init-Sequenz fuer komplett neue Apps (laut das Auth-Reference-Script):**
1. `GET /json/system/login?user=dssadmin&password=<PW>` (User-Login)
2. `GET /json/system/logout`
3. `GET /json/system/requestApplicationToken?applicationName=<NAME>`
4. App-Token notieren
5. Im dSS-Web approven

### App-Token einloggen (Session holen)

```
GET /json/system/loginApplication?loginToken=<APP_TOKEN>
→ {"result":{"token":"<SESSION_TOKEN>"},"ok":true}
```

### Session-Token-Lifetime

**Empirisch:** Working scripts refresh Session-Tokens alle 3 Minuten in seinem Auth-Script - die exakte Idle-Timeout-Dauer ist nicht offiziell dokumentiert, in der Praxis aber so kurz, dass eine **Auto-Re-Login-Strategie bei jedem 401/`not authorized`-Response** zwingend ist.

**Library-Strategie:**
- Token-Cache mit Use-Counter
- Bei Response `{"ok": false, "message": "not authorized"}` oder HTTP 401: re-login transparent, dann Request retryen
- KEINE Pre-Emptive Refresh-Loops (waeren reiner Traffic-Overhead)

### Bekannte Apps auf der Test-Box

- `ioBrokerScript` (Tokenpraefix `<other-token-prefix>`) - ioBroker-Adapter + eigene JS-Scripts
- `HomeAssistant` (Tokenpraefix `<app-token-prefix>`) - unsere neue HA-Integration (erstellt 2026-05-14)

Beide koennen parallel laufen - der dSS unterscheidet pro Anwendungs-Token. App-Token ist permanent gueltig solange er im dSS approved bleibt.

## System-Info-Endpoints (read-only, sicher)

| Endpoint | Result-Beispiel |
|----------|------------------|
| `/json/system/version` | `{"version":"dSS v1.54.0 (1.19.11.1-beta01-dirty)...","distroVersion":"1.19.11.1","EthernetID":"..."}` |
| `/json/system/time` | `{"time":1779999828,"offset":7200,"daylight":true,"timezone":"CEST"}` |
| `/json/system/getDSID` | `{"dSID":"302ed89f43f02ba000011efc","dSUID":"302ed89f43f0000000002ba000011efc00"}` |
| `/json/apartment/getName` | `{"name":"<apartment-name>"}` |

## Struktur-Endpoints

### Apartment-Struktur

```
GET /json/apartment/getStructure
→ Komplettes Tree (auf der Test-Box: 834 KB JSON)
```

Wichtige Felder:
- `apartment.clusters[]` - Cluster-Gruppen (die Test-Box hat **1 Cluster**: "Windschutz Jalousien", id=16, applicationType=2)
- `apartment.zones[]` - alle Zonen inkl. System-Zonen
- `zones[].devices[]` - Geraete pro Zone mit `dSID`, `dSUID`, `name`, `hwInfo`, `outputMode`
- `zones[].groups[]` - Standard 15 Groups + Custom-Groups

### die Test-Box-Inventar (verifiziert 2026-05-28)

- **20 Zonen total** (17 User-Zonen + 3 System-Zonen: 0, 65534, 14368)
- **274 Devices** total (137 davon in Broadcast-Zone 0)
- **1 Cluster** (Windschutz Jalousien)
- **41 Groups** in Zone 0

System-Zonen erkennen:
- Zone 0 = Apartment-Broadcast (alle Devices)
- Zone 14368 = Cluster-Zone (Windschutz Jalousien)
- Zone 65534 = unbenannt, 0 devices, 14 groups - vermutlich System-Reserved

### Devices

```
GET /json/apartment/getDevices
→ [{"id":"<DSID>","DisplayID":"...","dSUID":"<DSUID>","GTIN":"...","name":"...","model":"...","outputMode":<int>,...}]
```

`dSUID` ist der primaere Identifier (Modern), `dSID` der legacy ID. Beide werden parallel verwendet - in Events oft `originDSUID`, in Property-Tree-Pfaden `dsuid`.

### Circuits (dSM-Meter)

```
GET /json/apartment/getCircuits
→ {"circuits":[{"name":"dSM Meter FI20","dsid":"...","dSUID":"..."},...]}
```

### Sensor-Werte (langsam!)

```
GET /json/device/getSensorValue?dsid=<DSID>&sensorIndex=<I>
```

DS485-Bus-Roundtrip pro Sensor. **Lieber `deviceSensorEvent` und `zoneSensorValue` abonnieren** statt pollen.

## Circuit-Power-Monitoring + Outage-Detection

Live-verifiziert 2026-05-29 durch FI20-Blackout-Test (Skript: `discovery/circuit_blackout_test.py`, Daten: `archive/circuit_blackout.log`).

### Endpoints fuer Power-Werte

```
GET /json/apartment/getConsumption
    → {"result":{"consumption": <int>}, "ok": true}                         # Total-W ueber alle Circuits

GET /json/circuit/getConsumption?id=<KURZE_DSID>
    → {"result":{"consumption": <int>}, "ok": true}                         # W pro Circuit

GET /json/circuit/getEnergyMeterValue?id=<KURZE_DSID>
    → {"result":{"meterValue": <int_Ws>}, "ok": true}                       # Total-Energy in Watt-Sekunden
```

**KRITISCH:** `circuit/getConsumption` und `circuit/getEnergyMeterValue` brauchen die **kurze dSID** (24 Zeichen, ohne Padding), NICHT die lange dSUID (33 Zeichen). Beispiel:

- `dSUID: 302ed89f43f0000000002c8000001a3500` (33 chars, mit `0000000000` Padding)
- `dSID:  302ed89f43f02c8000001a35` (24 chars)
- Mapping: `dsid = dsuid[:11] + dsuid[22:33]`

Wer es falsch macht: `{"ok":false,"message":"Failed to parse dsid dsid_str:... ds485types.cpp:83"}`.

### Property-Tree pro dSM (volle Daten + Status-Flags)

Pfad: `/apartment/dSMeters/<DSUID>/`

| Property | Type | Bedeutung |
|----------|------|-----------|
| `dSUID` | string | Lange ID (33 chars) |
| `dSID` | string | Kurze ID (24 chars) |
| `DisplayID` | string | UI-Name |
| `name` | string | "dSM Meter FI20" etc. |
| `powerConsumption` | integer | aktuelle Watt-Anzeige |
| `powerConsumptionAge` | string | "0 s" / "5 s" etc. (string!) |
| `energyMeterValue` | floating | Total Energy (Wh) |
| `energyMeterValueWs` | floating | Total Energy (Ws) |
| `energyMeterAge` | string | "0 s" / "10 s" etc. |
| **`isValid`** | boolean | dSM ist auf dem Bus erreichbar + initialisiert |
| `isInitialized` | boolean | dSM-Boot-State |
| **`present`** | boolean | **KEY-MARKER:** Stromzufuhr aktiv |
| **`state`** | integer | **KEY-MARKER:** Live-State (0=normal, 1=powering-up, 255=offline-long) |
| `authorized` | boolean | dSS hat Berechtigung |
| `busMemberType` | integer | Hardware-Klasse |
| `hardwareVersion`/`softwareVersion`/`armSoftwareVersion`/`dspSoftwareVersion` | mixed | FW-Versionen |
| `hardwareName` | string | "dSM12" etc. |
| `isUpToDate` | boolean | FW aktuell |
| `apiVersion` | integer | dSM-API-Version |
| `zones`, `devices`, `powerStates` | none | Subtrees |
| `ignoreActionsFromNewDevices`, `ConfigURL`, `ModelUID`, `HardwareGuid`, `HardwareModelGuid`, `ImplementationId`, `VendorGuid`, `OemGuid`, `OemModelGuid` | misc | Metadata |

### Verifizierte Status-Patterns (FI20-Blackout-Test 2026-05-29)

5 unterschiedliche Status-Phasen identifiziert:

| Phase | Beobachtete Werte | Bedeutung | Dauer im Test |
|-------|-------------------|-----------|---------------|
| **NORMAL** | `W=62, isValid=True, present=True, state=0` | Standard-Betrieb | Baseline |
| **OFFLINE_FRESH** | `W=0, isValid=True, present=False, state=0` | Sicherung gerade gefallen | ~30s nach Aus |
| **POWERING_UP_INIT** | `W=132, isValid=False, present=True, state=1` | Stromzufuhr zurueck, dSM noch nicht authentifiziert | ~6s Uebergang |
| **POWERING_UP** | `W=128, isValid=True, present=True, state=1` | dSM aktiv, aber noch Frisch-Aufgewacht-Marker | ~4 Min nach Einschalten |
| **OFFLINE_LONG** | `W=???, isValid=False, present=False, state=255` | dSM ist seit langem tot (z.B. F6 auf der Test-Box) | Tage |

### Outage-Detection-Regeln (fuer Alarming)

Reihenfolge der Auswertung wichtig:

1. **`state == 255`** → **UNREACHABLE** (langfristig tot, definitiv Alarm)
2. **`present == False`** → **OFFLINE** (gerade Sicherung gefallen)
3. **`isValid == False AND state == 1`** → **POWERING_UP** (Recovery-Phase, kein Alarm)
4. **`state == 1`** → **RECENTLY_RECOVERED** (~4 Min Nachlauf nach Wiederherstellung)
5. **Default** → **NORMAL**

**`W == 0` allein ist NICHT zuverlaessig** - kann legitim Standby in der Nacht sein. Immer mit `present == False` koppeln.

### Virtual Circuits (z.B. Hue-Bridge)

die Test-Box hat einen Eintrag namens "Hue" als virtueller Circuit:
- `circuit/getConsumption` → `Failed to parse dsid` (Function fehlt)
- Property-Tree: `present=True, isValid=True, state=0`, aber `powerConsumption` ist 0 oder fehlt

**Erkennung:** Wenn `getConsumption` mit Bus-Error scheitert + Property `present=True`, dann ist es ein virtueller Circuit. Aus Power-Monitoring ausschliessen.

### Energy-Meter (kWh-Counter)

- `energyMeterValue` ist eine **monoton steigende Floating-Point-Zahl in Wh**
- Beispiel-Werte aus die Test-Box: 751.903.625 / 1.647.913.448 / 231.275.165
- Diff zwischen zwei Lesungen = verbrauchte Energie im Zeitraum
- Bei Outage ist der Wert "eingefroren" - kein Reset nach Wiedereinschalten

### Apartment-Total

`apartment/getConsumption` liefert die Summe aller present-dSMs in Watt. Wichtig: F6 (lange offline) erscheint nicht in der Summe - das ist ein Feature, kein Bug.

## Steuerungs-Endpoints

### Szene aufrufen (Zone-Level)

```
GET /json/zone/callScene?id=<ZONE>&groupID=<G>&sceneNumber=<S>
→ {"ok":true}
```

### Szene Undo

```
GET /json/zone/undoScene?id=<ZONE>&groupID=<G>
→ {"ok":true}
```

### Geraete direkt

```
GET /json/device/turnOn?dsid=<DSID>
GET /json/device/turnOff?dsid=<DSID>
GET /json/device/setValue?dsid=<DSID>&value=<0-255>
GET /json/device/setOutputValue?dsid=<DSID>&offset=<OUT>&value=<V>  # Jalousien-Position
```

### Group-IDs (verifiziert auf der Test-Box)

- Group 1 = Licht (yellow)
- Group 2 = Jalousien (grey)
- 0, 3-9 = System-Groups (broadcast, heating, etc.)

## Event-Endpoints (Long-Polling)

### Subscribe

```
GET /json/event/subscribe?name=<EVENT_NAME>&subscriptionID=<ID>
→ {"ok":true}
```

`subscriptionID` ist client-gewaehlt (int, beliebig). **Mehrere Events unter derselben ID buendeln** funktioniert (verifiziert: 13 Events unter ID 47110815, eine Polling-Connection).

### Get (Long-Poll)

```
GET /json/event/get?subscriptionID=<ID>&timeout=<MS>
→ {"result":{"events":[{...},{...}]},"ok":true}
```

- `timeout` in ms, dSS akzeptiert bis 60000+
- Bei Event(s): sofortige Antwort
- Bei Timeout: leere `events`-Array
- Sofort reconnecten

### Unsubscribe

```
GET /json/event/unsubscribe?name=<EVENT_NAME>&subscriptionID=<ID>
→ {"ok":true}
```

Optional - Subscriptions idle-out automatisch nach Disconnect.

## Verifizierte Event-Payloads (aus Live-Discovery 2026-05-28)

Alle Events haben drei Top-Level-Felder:
- `name` (string) - Event-Typ
- `properties` (object) - Event-spezifische Daten
- `source` (object) - **immer vorhanden**, enthaelt Origin-Info

### Source-Objekt (universell)

```json
{
  "set":         ".zone(5).group(2)"  oder  "dsid(303505d7f80...)"  oder  "" (apartment-level),
  "groupID":     <int>,
  "zoneID":      <int>,
  "dsid":        "<DSID>"  (nur bei isDevice=true),
  "isApartment": <bool>,
  "isGroup":     <bool>,
  "isDevice":    <bool>
}
```

`set` ist ein **dSS-Set-Notation-String** (ds-rules-Style) - sehr nuetzlich fuer Filter im Event-Dispatcher.

### `callScene`

```json
{
  "name": "callScene",
  "properties": {
    "zoneID":      "5",
    "groupID":     "2",
    "sceneID":     "17",
    "originToken": "",
    "callOrigin":  "9",
    "originDSUID": "303505d7f80000000000168000113edd00"
  },
  "source": {"set":".zone(5).group(2)","groupID":2,"zoneID":5,"isApartment":false,"isGroup":true,"isDevice":false}
}
```

**`callOrigin`-Werte (verifiziert):**
- `"9"` - Taster-/Sensor-getriggert (die meisten)
- (weitere Werte in laengeren Captures zu verifizieren)

### `undoScene`

Schema analog `callScene`, ohne `sceneID` (zurueck zur vorherigen Szene).

### `buttonClick`

```json
{
  "name": "buttonClick",
  "properties": {
    "buttonIndex": "0",
    "clickType":   "0"
  },
  "source": {
    "set":      "dsid(303505d7f80000000000168000113edf00)",
    "dsid":     "303505d7f80000000000168000113edf00",
    "zoneID":   5,
    "isApartment":false,"isGroup":false,"isDevice":true
  }
}
```

**Korrektur zur frueheren Doku:** `deviceID` ist NICHT in `properties` - die DSID/DSUID kommt aus `source.dsid`.

`clickType`-Werte (Standard dSS):
- 0 = single click
- 1 = double click
- 2 = triple click
- 3 = hold
- 4 = release (nach hold)

### `stateChange`

```json
{
  "name": "stateChange",
  "properties": {
    "callOrigin": "9",
    "statename":  "zone.5.light",
    "state":      "inactive",
    "value":      "2",
    "oldvalue":   "1"
  },
  "source": {"groupID":1,"zoneID":5,"isApartment":false,"isGroup":true,"isDevice":false}
}
```

`statename`-Muster:
- `zone.<id>.light` - Lichtstatus pro Zone (active/inactive)
- `zone.<id>.<group>` - andere Gruppen
- Custom-Names fuer User-Defined-States (siehe Addon-Section)

### `deviceBinaryInputEvent`

```json
{
  "name": "deviceBinaryInputEvent",
  "properties": {
    "inputState": "1",
    "inputIndex": "0",
    "inputType":  "1"
  },
  "source": {
    "set":"dsid(<DSID>)",
    "dsid":"<DSID>",
    "zoneID":9,
    "isDevice":true
  }
}
```

`inputType`-Beispiele:
- 1 = Bewegungsmelder / Praesenz
- weitere Typen je nach Geraet (Reed, Door-Contact, etc.)

### `deviceSensorEvent`

Aehnlich `zoneSensorValue` aber von einem konkreten Device (`source.dsid` gesetzt).

### `zoneSensorValue`

```json
{
  "name": "zoneSensorValue",
  "properties": {
    "originDSID":       "0000000000000000000000000000000000",
    "sensorValueFloat": "-11.9375",
    "sensorType":       "77",
    "sensorValue":      "1249"
  },
  "source": {"groupID":0,"zoneID":0,"isApartment":false,"isGroup":true,"isDevice":false}
}
```

`sensorType`-Werte (Standard dSS):
- 9 = Raumtemperatur
- 76 = Helligkeit
- 77 = ggf. Aussentemperatur (die Test-Box: -11.9 Grad Float)
- weitere siehe dSS-Doku

### `zoneSensorError` (war nicht in alter Doku!)

```json
{
  "name": "zoneSensorError",
  "properties": {
    "lastValueTS": "1970-01-01T00:00:00.000Z",
    "sensorType":  "9"
  },
  "source": {"groupID":0,"zoneID":3,"isApartment":false,"isGroup":true,"isDevice":false}
}
```

Sensor liefert keine Werte mehr → 1970-Timestamp als Marker. Diagnostik-relevant.

### `highlevelevent`

```json
{
  "name": "highlevelevent",
  "properties": {
    "source-name": "Taster 4 Tisch Arbeiten - Zone 0 ein",
    "id":          "1610290942"
  },
  "source": {"groupID":0,"zoneID":0,"isApartment":true,"isGroup":false,"isDevice":false}
}
```

**Wichtig:** `properties.id` ist die `/usr/events/<id>` - das **referenzierte User-Defined-Action**. `properties.source-name` ist deren Name. So koennen wir auf der HA-Seite User-Actions als HA-Triggers exposen.

### Weitere abonnierte Events (in Capture-Phase nicht aufgetreten, aber Subscribe erfolgreich)

- `running` - dSS-Boot-Marker
- `model_ready` - Modell-Refresh-Marker
- `DeviceEvent` - generischer Device-Event
- `executionDenied` - Versuch eines Calls der durch Conditions abgelehnt wurde

## State-Endpoints (System States)

### Lesen / Setzen via /json/state

```
GET /json/state/get?name=<NAME>
GET /json/state/set?name=<NAME>&value=<V>
```

**Wichtig:** Die wirklich konfigurierten User-States stehen NICHT unter `/apartment/states` (das ist leer auf der Test-Box), sondern in `/scripts/system-addon-user-defined-states/...`. Siehe Addon-Section.

## Property-Tree (Generelle Konfiguration)

### Children listen / Werte lesen

```
GET /json/property/getChildren?path=<PATH>
GET /json/property/getString?path=<PATH>
GET /json/property/getInteger?path=<PATH>
GET /json/property/getBoolean?path=<PATH>
```

**Type-Mismatch-Fehler:** `getString` auf int-Feld → `{"ok":false,"message":"Property-Type mismatch: <field>"}`. Library muss vorher per `getChildren` die `type`-Information lesen oder beide Getter probieren.

### `getChildren`-Response-Format

```json
{
  "result": [
    {"name": "id",      "type": "string"},
    {"name": "offset",  "type": "integer"},
    {"name": "enabled", "type": "boolean"},
    {"name": "actions", "type": "none"}      ← "none" = subtree
  ],
  "ok": true
}
```

### Werte setzen

```
GET /json/property/setString?path=<PATH>&value=<V>
GET /json/property/setInteger?path=<PATH>&value=<V>
```

### `property/query` (Filter-Syntax)

```
GET /json/property/query?query=/scripts/<addon>/entries/*(field1,field2,...)&force=true
```

- `*` = Wildcard fuer eine Pfad-Stufe
- `(field1,field2)` = nur diese Felder pro Match zurueckgeben (sparsam)
- `force=true` = laden auch wenn nicht gecacht

**Response-Form:** **`result` ist ein Array** (nicht Map!) - jeder Match wird als Sub-Object zurueckgegeben.

Beispiel aus das Wecker-Reference-Script:
```
?query=/scripts/system-addon-timed-events/entries/21/*(offset)
→ {"result":{"21":[{},{},{},{},{"offset":29700},{},{},{}]},"ok":true}
```

Die Indizes im Array entsprechen der Position des `*`-Matches, die meisten Eintraege sind leer wenn das Field nicht im Sub-Tree existiert. **Counter-Intuitive** - Library muss die `field`-Properties durchsuchen und nicht auf Reihenfolge verlassen.

**Pragmatische Alternative:** `getChildren` + getypte Getter ist robuster.

## System-Addon-Architektur (KRITISCH!)

Die `/apartment/...`-Subtree-Pfade in der alten Doku (`states`, `scripts`, `timed_events`, `scene_responders`) **sind alle leer** auf der Test-Box. Die ganze User-Konfiguration lebt unter `/scripts/<addon-name>/...`.

### Installierte Addons (die Test-Box, 17 total)

```
/scripts/
├── event-mailer                              - E-Mail-Versand
├── heating-controller                        - alt/legacy
├── led-wizard                                - LED-Konfig
├── motion-detector                           - Bewegungsmelder-Logik
├── solar_computer                            - Solar
├── system-addon-heating-controller           - Heizung
├── system-addon-presence-simulator           - Anwesenheits-Simulator
├── system-addon-remote-connectivity          - Cloud (my.digitalstrom)
├── system-addon-scene-responder              - **Szene-Responder (102 entries auf der Test-Box)**
├── system-addon-timed-events                 - **Zeitschaltuhr (17 entries auf der Test-Box)**
├── system-addon-user-defined-actions         - **Container fuer User-Actions** (Inhalte in /usr/events!)
├── system-addon-user-defined-states          - User-States Definitionen
├── system-addon-user-defined-states-helper   - State-Helpers
├── system-addon-ventilation-controller       - Lueftung
├── system_hail                               - Hagelschutz
├── system_state                              - System-State-Tracker
└── vdc-ui-hue                                - Hue-Integration im dSS (Bridge-Wrapper)
```

### Universelles Addon-CRUD-Pattern

**Read:**
```
GET /json/property/getChildren?path=/scripts/<addon>/entries
GET /json/property/getChildren?path=/scripts/<addon>/entries/<id>
GET /json/property/getString?path=/scripts/<addon>/entries/<id>/<field>
```

**Write (nicht via property/setX, sondern via Event-Raise!):**
```
GET /json/event/raise?name=<addon>.config&parameter=actions=save;value=<JSON_STRING>
```

Der `parameter`-String ist Semikolon-getrennt, `value=<JSON>` ist der komplette Entry als JSON-stringified.

Beispiel aus das Wecker-Reference-Script (vereinfacht):
```
name = "system-addon-timed-events.config"
parameter = "actions=save;value={\"id\":\"21\",\"name\":\"Wecker einschalten\",\"time\":{\"offset\":29700,\"timeBase\":\"daily\"},...}"
```

**Andere `actions=`-Werte** (vermutet, in Discovery noch nicht systematisch verifiziert):
- `save` - create or update (idempotent ueber `id`)
- `delete` - loeschen
- `execute` - ggf. direkt triggern

## Addon: `system-addon-timed-events` (Zeitschaltuhr)

Pfad: `/scripts/system-addon-timed-events/entries/<id>/`

### Entry-Schema (verifiziert: Wecker entry 21)

```json
{
  "id":            "21",
  "name":          "Wecker einschalten",
  "scope":         "system-addon-timed-events",
  "conditions":    {"enabled": true},
  "time": {
    "timeBase":       "daily" | "weekly" | "sunrise" | "sunset" | ...,
    "offset":         29700,                    // Sekunden ab Mitternacht / Sonnenauf-/untergang
    "recurrenceBase": "weekly",
    "recurrence":     {"0":"MO","1":"TU","2":"WE","3":"TH","4":"FR","5":"SA","6":"SU"}
  },
  "actions": {
    "0": {
      "type":     "custom-event" | "device-scene" | "zone-scene" | ...,
      "event":    "1626014704",                  // bei type=custom-event
      "delay":    0,
      "category": "manual"
    }
  },
  "deleteCounter": 0,
  "lastExecuted":  "2026-05-28 08:15:00"
}
```

### Update-Pattern

```
event-raise name=system-addon-timed-events.config
parameter=actions=save;value=<JSON_STRINGIFIED_ENTRY>
```

die Test-Box hat 17 Eintraege (IDs 1-9, 13-14, 17, 19-23). Ein dump aller Entries liegt in `archive/scripts_addons.json` unter `system-addon-timed-events.entries.<id>`.

## Addon: `system-addon-scene-responder` (Szene-Responder)

Pfad: `/scripts/system-addon-scene-responder/entries/<id>/`

**102 Eintraege** auf der Test-Box. Triggern Aktionen wenn ein State sich aendert oder eine Szene aufgerufen wird.

### Entry-Schema (verifiziert: entry 17)

```json
{
  "id":                "17",
  "name":              "Automat Beleuchtung Sitzplatz Sued ausschalten",
  "scope":             "system-addon-scene-responder",
  "technicalRole":     "system",
  "persistentScope":   true,
  "delay":             0,
  "conditions":        {"enabled": true},
  "triggers": {
    "1": {
      "type":     "addon-state-change",
      "addon-id": "system-addon-user-defined-states",
      "name":     "1561580731",                  // State-ID die getrackt wird
      "state":    "inactive"                     // Trigger-Wert
    }
  },
  "actions": {
    "0": {
      "type":     "zone-scene",
      "zone":     4,
      "group":    1,
      "scene":    0,
      "force":    false,
      "delay":    0,
      "category": "manual"
    }
  },
  "singularTriggered": false,
  "initialTriggered":  false,
  "lastExecuted":      "2026-05-28 21:14:04"
}
```

### Trigger-Typen (in the test user's 102 Eintraegen beobachtet)

- `addon-state-change` - User-State wechselt zu bestimmtem Wert
- (weitere Typen wie `zone-scene-call`, `button-press`, `device-event` vermutet, noch nicht systematisch geprueft)

### Spezielle ID-Praefixe

Einige Entries haben statt numerischer IDs **Praefix-Patterns** wie `md_9_turn_on_present` - das sind System-generierte Responder fuer das `motion-detector`-Addon (Zone 9, Praesenz-Logik etc.). Library sollte solche Eintraege als "system-managed" markieren, nicht direkt editierbar.

## Addon: `system-addon-user-defined-actions`

Pfad: `/scripts/system-addon-user-defined-actions/` (Container)

Die **eigentlichen User-Actions** stehen unter `/usr/events/<id>` (NICHT unter `/scripts/...`). Das Addon-Pfad enthaelt nur Metadaten/Container-Info.

### `/usr/events/<id>` Schema (User-Defined-Actions)

```json
{
  "id":          "1471955518",
  "name":        "Markise Sitzplatz Sued schliessen",
  "source":      "system-addon-user-defined-actions",
  "disabled":    false,
  "lastSaved":   "1597566793179",
  "lastExecuted":"2026-05-27 00:00:05",
  "conditions": {
    "states": {
      "zone.18.light": "2"                 // System-State (Format: <namespace>.<value>)
    },
    "addon-states": {
      "system-addon-user-defined-states": {
        "1561580731": 2                    // User-State-ID: gewuenschter int-Wert
      }
    }
  },
  "actions": {
    "0": {
      "type":     "device-scene",
      "dsuid":    "303505d7f800000000000f40000aa8d600",
      "scene":    14,
      "force":    false,
      "delay":    0,
      "category": "manual"
    }
  }
}
```

### Action-Typen (alle 9 in die Test-Box vorhanden, total 156 Actions verteilt auf 69 Events)

| Type | Required | Anzahl in die Test-Box |
|------|----------|--------------------|
| `device-scene` | `dsuid, scene, force, delay, category` | 55 |
| `zone-scene` | `zone, group, scene, force, delay, category` | 54 |
| `url` | `url, delay, category` (HTTP GET zu beliebiger URL) | 17 |
| `change-addon-state` | `statename, addon-id, state, delay, category` | 12 |
| `device-blink` | `dsuid, delay, category` | 10 |
| `custom-event` | `event` (ID auf anderes /usr/events), `delay, category` | 4 |
| `undo-zone-scene` | `zone, group, scene, force, delay, category` | 2 |
| `change-state` | `statename, state, delay, category` | 1 |
| `zone-blink` | `zone, group, delay, category` | 1 |

**Container-Pattern:** Ein "Wecker"-Action triggert via `type:custom-event` mehrere andere User-Actions (Audio ein, Licht ein, etc.). Verschachtelung wird vom dSS aufgeloest.

**`type:url`** ist sehr maechtig - users typically use das fuer NEEO-Steuerung (`http://192.168.40.30:8087/set/neeo.0.rooms.../powerToggle?value=true`). Wir koennten das gleiche fuer HA-Webhook-Calls nutzen.

### Conditions-Modell

- `states.<statename>` - System-State-Bedingung (Wert als String)
- `addon-states.<addon-id>.<state-id>` - User-State-Bedingung (Wert als int)
- `enabled` - simpler boolean (vor allem in scene-responder + timed-events)

### Triggern aus dem dSS

User-Actions werden NICHT direkt aus `/usr/events/<id>` getriggert, sondern via High-Level-Event:

```
GET /json/event/raise?name=highlevelevent&parameter=actionName=<NAME>
```

Wenn der dSS-Server die Action ausgefuehrt hat, kommt zusaetzlich ein **`highlevelevent`-Event** auf der Subscription mit `properties.id = "<event-id>"` und `properties.source-name = "<name>"`. Das ist unser HA-Hook fuer "User-Action wurde ausgefuehrt".

## Addon: `system-addon-user-defined-states`

Pfad: `/scripts/system-addon-user-defined-states/`

Untergliedert sich in (verifiziert):

```
/scripts/system-addon-user-defined-states/
├── device-sensor-states/    - States die an Sensor-Werte gekoppelt sind
├── custom-states/           - User-Tastatur-States (manuell schaltbar)
├── combined-states/         - Aggregation mehrerer States (UND/ODER)
├── triggered-states/        - Time-/Event-getriggerte States
└── migratedVentilation36To6 - Migrations-Flag (boolean)
```

State-Werte: int (0/1/2 typischerweise), referenziert ueber state-ID (numerisch) wie `1561580731` in den Conditions oben.

Schemas pro Sub-Kategorie variieren - siehe `archive/scripts_addons.json` fuer Live-Beispiele.

## Addon: `event-mailer`

Pfad: `/scripts/event-mailer/`

die Test-Box: 2 top-keys. Verkettet sich mit `/usr/triggers/<id>` ueber `triggerPath=/scripts/event-mailer/values/<n>` und `relayedEventName=event-mailer`. Konfiguration der Mail-Texte vermutlich unter `values/`.

API fuer Mail-Send via Event-Raise (vermutet, nicht verifiziert):
```
GET /json/event/raise?name=event-mailer.config&parameter=...
```

## Addon: `system-addon-presence-simulator`

Pfad: `/scripts/system-addon-presence-simulator/`

Anwesenheits-Simulation (Ferien-Modus). 3 top-keys.

## Addon: `system-addon-heating-controller` + `heating-controller`

Pfade: zwei separate Trees fuer Heizung (vermutlich legacy + neu parallel). Je 14 top-keys.

Triggers via `/usr/triggers/<id>` mit `triggerPath=/scripts/system-addon-heating-controller/systemTriggers/<action>` und `relayedEventName=heating-controller.operation-mode`.

## Addon: `vdc-ui-hue`

Pfad: `/scripts/vdc-ui-hue/`

dSS-interne Hue-Integration (Hue Bridge gekoppelt am dSS). Aus Migrations-Sicht: wir wollen das in HA mit der nativen Hue-Integration ersetzen, nicht ueber dSS routen.

## `/usr/triggers` - Event-Relay-Mapping

145 Eintraege auf der Test-Box. Jeder mappt einen Property-Pfad auf einen Event-Namen:

```json
{
  "id": 1,
  "triggerPath":                  "/scripts/system-addon-heating-controller/systemTriggers/absent",
  "relayedEventName":             "heating-controller.operation-mode",
  "additionalRelayingParameter":  "actions=reactOnAbsent"
}
```

Wenn der Pfad sich aendert, feuert der dSS automatisch das `relayedEventName`-Event mit dem `additionalRelayingParameter`. Das ist der **Glue-Mechanismus** zwischen Property-Tree-Updates und dem Event-System.

## `/usr/events` vs `/scripts/<addon>` - Konzeptueller Unterschied

| Ort | Zweck | Owner |
|-----|-------|-------|
| `/usr/events/<id>` | **User-erstellte Actions** mit conditions+actions | User (via Web-UI oder Library) |
| `/scripts/<addon>/entries/<id>` | **Addon-spezifische Configs** (Schedule, Responder, etc.) | Addon-Code |
| `/usr/triggers/<id>` | **System-Event-Bindings** (Property-Pfad → Event) | meist System, einige durch Addons gepflegt |

User-Actions referenzieren sich gegenseitig per `custom-event`-Type, Timed-Events und Responder triggern via `custom-event` die User-Actions, und das alles laeuft am Ende durch den Event-Bus.

## High-Level-Events (User-Actions ausloesen)

```
GET /json/event/raise?name=highlevelevent&parameter=actionName=<NAME>
```

die Test-Box hat 69 solche Actions (siehe `/usr/events/*` mit `source=system-addon-user-defined-actions`).

## Gotchas / Lessons (verifiziert)

| Gotcha | Was tun |
|--------|---------|
| Session-Token expirt schnell (Working scripts refresh alle 3min als Sicherheit) | Auto-Re-Login bei 401 / "not authorized" |
| Subscribe ist nicht persistent ueber Server-Side-Disconnect | Bei Event `running` re-subscriben |
| `getSensorValue` ist DS485-Bus-Roundtrip (sehr langsam) | Lieber Events abonnieren |
| Sensoren senden Events nur bei Aenderung | Initial-Wert holen, dann Events |
| Zone-IDs 0, 14368, 65534 sind System-Zonen | Filter beim Zonen-Auflisten |
| `callOrigin` ist nicht stabil dokumentiert | Empirisch sammeln, oder Source-Set-Notation auswerten |
| Source-Set-Notation (`.zone(5).group(2)` etc.) ist DER bessere Filter als isApartment/isGroup/isDevice | In Library als parsed Set-Object exposen |
| `property/query` mit `(field)`-Filter gibt Array zurueck, nicht Map | Lieber `getChildren`+getypte Getter |
| `getString` auf int-Field → Type-Mismatch-Error | Vorher `type` aus `getChildren` lesen |
| Property-Pfade in alter Doku (`/apartment/states` etc.) sind LEER | Korrekte Pfade: `/scripts/<addon>/...` und `/usr/events/...` |
| Addon-Configs werden ueber `event/raise name=<addon>.config` veraendert | NICHT `property/setString` versuchen |
| Scene-Responder hat System-Auto-Eintraege mit Praefix (`md_*`) | Diese nicht als User-Editable exponieren |
| User-Actions koennen sich gegenseitig per `custom-event` referenzieren | Library braucht Resolution-Graph fuer Zyklus-Erkennung |

## Endpoints noch zu verifizieren

- Genaues Verhalten von `event/raise` mit `actions=save` vs `actions=delete` vs `actions=execute`
- Vollstaendige `actions=`-Werte-Liste pro Addon
- Cloud-User-Management Schema unter `system-addon-remote-connectivity`
- Vollstaendige `triggers[].type`-Liste im Scene-Responder
- Sensor-Type-Codes (siehe Discovery: 9, 76, 77 beobachtet - vollstaendige Liste in dSS-Doku oder weitere Captures)
- `clickType`-Werte ueber 4 hinaus (Position-Hold-Variants etc.)
- Cluster-API: die Test-Box hat 1 Cluster (Windschutz Jalousien) - wie wird der gesteuert?

Diese Punkte sind nicht kritisch fuer v0.1, koennen mit einem zweiten Discovery-Run zur Laufzeit verifiziert werden.
