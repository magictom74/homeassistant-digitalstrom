# Architektur: HA-Integration digitalSTROM

## Grundprinzip

```
┌─────────────────────────────────────────────────────────────┐
│                    dSS (digital STROM Server)                │
│                       <dss-host>:8080                     │
│                                                              │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────────────┐ │
│  │  Geraete    │  │  Apps        │  │  System             │ │
│  │             │  │              │  │                     │ │
│  │  - Lichter  │  │ - Zeitschalt │  │  - States           │ │
│  │  - Jalousien│  │ - Responder  │  │  - Actions          │ │
│  │  - Sensoren │  │ - Cloud      │  │  - Property-Tree    │ │
│  │  - Buttons  │  │ - E-Mail     │  │  - Energy-Meter     │ │
│  └─────────────┘  └──────────────┘  └─────────────────────┘ │
│                                                              │
│             REST-API (HTTPS, self-signed Cert)               │
└──────────┬───────────────────────────────────────────┬───────┘
           │                                           │
           │ HTTP Commands                             │ Long-Poll
           │ (kurze Connections)                       │ Event-Stream
           │ on-demand                                 │ persistent
           ↓                                           ↑
┌─────────────────────────────────────────────────────────────┐
│                  pydigitalstrom (Library)                    │
│                                                              │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────────────┐ │
│  │  Client     │  │  Models      │  │  Event-Loop         │ │
│  │             │  │              │  │                     │ │
│  │  - Auth     │  │  - Device    │  │  - Subscribe        │ │
│  │  - Request  │  │  - Zone      │  │  - Long-Poll        │ │
│  │  - Errors   │  │  - Scene     │  │  - Reconnect        │ │
│  │             │  │  - State     │  │  - Dispatch         │ │
│  └─────────────┘  └──────────────┘  └─────────────────────┘ │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           │ Python async API
                           │ + Event-Callbacks
                           ↓
┌─────────────────────────────────────────────────────────────┐
│       custom_components/digitalstrom (HA Integration)        │
│                                                              │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────────────┐ │
│  │  Setup      │  │  Entities    │  │  Services           │ │
│  │             │  │              │  │                     │ │
│  │  - __init__ │  │  - Light     │  │  - call_scene       │ │
│  │  - config_  │  │  - Cover     │  │  - undo_scene       │ │
│  │    flow     │  │  - Sensor    │  │  - set_state        │ │
│  │  - coord-   │  │  - Scene     │  │  - trigger_action   │ │
│  │    inator   │  │  - Button    │  │  - send_email       │ │
│  │             │  │  - Select    │  │  - schedule_add     │ │
│  │             │  │  - Sensor    │  │  - responder_toggle │ │
│  │             │  │    (Diag)    │  │                     │ │
│  └─────────────┘  └──────────────┘  └─────────────────────┘ │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           │ HA Entity-API / Service-Registry
                           ↓
┌─────────────────────────────────────────────────────────────┐
│              Home Assistant Core / Frontend                  │
│                                                              │
│   Dashboards | Automations | Voice Assistants | Mobile App   │
└─────────────────────────────────────────────────────────────┘
```

## Schichten-Trennung

### Library `pydigitalstrom/`

Reines API-Wrapping, **null HA-Abhaengigkeit**:
- `client.py` - HTTPClient (httpx), Auth-Flow, Reconnect-Strategy
- `apartment.py` - Apartment-Struktur-Modell (Zonen, Gruppen, Apparate)
- `device.py` - Device-Abstraktion (dSID, hwType, outputMode)
- `scene.py` - Scene-Operations
- `state.py` - State-Read/Write
- `events.py` - Event-Subscription + Dispatcher
- `exceptions.py` - DSSError, DSSAuthError, DSSConnectionError, DSSTimeout

Library ist **standalone testbar**, kann von anderen Python-Projekten benutzt werden.

### Integration `custom_components/digitalstrom/`

HA-spezifischer Glue-Code:
- `__init__.py` - Setup-Logik, Hub-Klasse, ConfigEntry-Verarbeitung
- `config_flow.py` - "Add Integration"-Dialog: Host, Port, App-Token
- `coordinator.py` - DataUpdateCoordinator (Single-Source-of-Truth fuer Entities)
- `manifest.json` - Metadaten, Dependencies (pydigitalstrom)
- `const.py` - DOMAIN, DEFAULT_PORT, Event-Names, Service-Names
- `light.py`, `cover.py`, `sensor.py`, `binary_sensor.py`, `scene.py`, `button.py`, `select.py` - Platform-Module
- `event.py` - Button-Click-Events
- `diagnostics.py` - Diagnostics-Dump fuer Support
- `services.yaml` - Service-Definitionen + Schema
- `strings.json` + `translations/` - UI-Strings (en, de)

## Event-Flow im Detail

### Befehl (HA → dSS)

```
User klickt Lampe an
    → light.async_turn_on(brightness=200)
    → Integration → coordinator.async_set_brightness(device_id, 200)
    → pydigitalstrom.Device.set_value(200)
    → HTTP POST /json/device/setValue?dsid=X&value=200&token=T
    → dSS schaltet Lampe
    → HTTP 200 OK
    → coordinator.async_request_refresh() (Optimistic-Update)
```

Latenz: ca. 50-200ms (LAN-Roundtrip + dSS-Internal-Bus-Time).

### Aenderung im dSS (dSS → HA)

```
Schalter wird physisch gedrueckt
    → dSS verarbeitet → fuehrt Szene aus (z.B. Wohnzimmer-Licht-An)
    → dSS feuert Event "callScene" auf Event-Bus
    → pydigitalstrom Long-Poll-Connection erhaelt das Event
    → Event-Dispatcher → coordinator.async_handle_event(callScene_payload)
    → coordinator updated betroffene Devices (Lichter im Zonenbereich)
    → HA Entities feuern state_changed → Frontend aktualisiert
    → Automations werden getriggert (falls konfiguriert)
```

Latenz: sub-second, ca. 100-500ms je nach dSS-Auslastung.

## Authentication-Flow

```
1. (Einmalig im dSS-Web) User legt Application "HomeAssistant" an
   → dSS gibt Application-Token zurueck (permanent gueltig)
   → User approved den Token via dSS-Web

2. (Bei HA-Integration-Setup) User gibt Token in Config-Flow ein

3. (Bei jedem HA-Start) Integration ruft:
   GET /json/system/loginApplication?loginToken=<APP_TOKEN>
   → erhaelt Session-Token (gueltig fuer aktive Session)

4. (Bei jedem API-Call) Session-Token wird als token= Parameter mitgeschickt:
   GET /json/zone/callScene?id=5&token=<SESSION_TOKEN>

5. (Bei Session-Expiry / 401) Integration loggt sich automatisch neu ein via loginApplication
```

App-Token + auto-relogin = stabil ueber Tage/Wochen ohne User-Intervention.

## Event-Subscription-Strategy

Aktive Subscriptions beim Start:
- `callScene` - Szene-Aufrufe
- `undoScene` - Szene-Undos
- `buttonClick` - Tasten-Events
- `stateChange` - State-Aenderungen
- `deviceBinaryInputEvent` - Binary-Sensoren
- `deviceSensorEvent` - Wert-Sensoren
- `running` - dSS-Reboot-Marker
- `model_ready` - Modell-Refresh-Marker
- `highlevelevent` - User-Actions
- `cloud.status_change` - Cloud-Connection-Aenderungen

Subscription-ID generiert beim Start, wird bei Reconnect wieder genutzt (dSS haelt Buffer).

## Reconnect / Resilience

| Szenario | Strategy |
|----------|----------|
| Long-Poll-Timeout (kein Event) | Sofort reconnect, gleiche Subscription-ID |
| Network-Error | Exponential backoff, max 60s |
| 401 Unauthorized | Re-Login via App-Token, dann retry |
| dSS-Reboot (Event `running`) | Apartment-Struktur neu laden, Entities re-registrieren |
| 5xx Server-Error | Retry mit Backoff, nach 3 Fehlern Event in HA Repairs |

## Datenfluss bei Setup

```
HA Start
  → ConfigEntry geladen
  → __init__.async_setup_entry()
  → Hub-Object erstellt (mit pydigitalstrom.Client)
  → Login via App-Token
  → Initial Apartment-Struktur fetchen → in Coordinator legen
  → Device-Registry-Eintraege erstellen (Hub als Parent-Device, Zonen als Areas)
  → Platform-Forwards: light, cover, sensor, scene, etc.
  → Jede Platform liest Coordinator-Daten und erstellt Entities
  → Event-Loop wird gestartet als Background-Task
  → Setup-Complete-Signal
```

## Tests-Strategie

- **Unit-Tests** (pydigitalstrom): Mock-HTTPClient, isolierte Modell-Tests
- **Integration-Tests** (pydigitalstrom): Recorded API-Responses (VCR.py), kein Live-dSS noetig
- **HA-Integration-Tests** (custom_components): pytest-homeassistant-custom-component, Mock-Coordinator
- **End-to-End**: gegen echte dSS-Box (<dss-host>) in Test-Phasen

## Distribution

- **GitHub-Repo:** `ha-integration-digitalstrom` (Mono-Repo)
- **HACS:** `hacs.json` im Root, kompatibel als "integration" type
- **PyPI:** `pydigitalstrom` als separate Library (optional, fuer V1.0+)
- **HA Brand:** Falls Submission an HA Core: Brand-PR im `home-assistant/brands` Repo
