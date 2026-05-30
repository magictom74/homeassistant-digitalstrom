# Library Design: `pydigitalstrom`

Vollstaendige Spezifikation der HA-agnostischen Python-Library `pydigitalstrom`. Jede Funktion und jedes Datenmodell ist hier dokumentiert, sodass beliebige Integrationen (HA, CLI, Skripte, andere Frameworks) gegen eine stabile API bauen koennen.

**Status:** Design-Spec, Implementation pending (Phase C).

## Inhalt

- [1. Goals](#1-goals)
- [2. Top-Level Exports](#2-top-level-exports)
- [3. Module-Reference](#3-module-reference)
- [4. Object-Model (Dataclasses)](#4-object-model-dataclasses)
- [5. Event-Modell](#5-event-modell)
- [6. Addon-Module](#6-addon-module)
- [7. Exceptions](#7-exceptions)
- [8. CLI-Tool](#8-cli-tool)
- [9. Test-Strategie](#9-test-strategie)
- [10. Konventionen](#10-konventionen)
- [11. Versions-Roadmap](#11-versions-roadmap)

---

## 1. Goals

1. **HA-agnostisch** - Library laeuft standalone, keine Home-Assistant-Imports
2. **Async-only** - kein sync-API, durchgaengig async/await
3. **Type-hinted vollstaendig** - mypy-strict-compliant
4. **Testbar isoliert** - alle Network-Calls ueber `DssClient`, Mock via `respx`-Fixtures aus `archive/`
5. **Models als immutable Dataclasses** - keine versteckten Dict-Returns
6. **Logging mit `[pydss]`-Prefix** - laut Enterprise-Rules
7. **Robust gegen dSS-Quirks** - Auto-Re-Login bei 401, Type-Mismatch-Tolerance, Reconnect-Strategie
8. **Versioniert + dokumentiert** - `pyproject.toml` mit Semver, Public-API stabil ab v1.0

## 2. Top-Level Exports

```python
# pydigitalstrom/__init__.py
from .client import DssClient
from .auth import AppToken
from .apartment import fetch_apartment
from .events import EventStream, EventDispatcher, DssEvent
from .models import (
    Apartment, Zone, Cluster, Group, Device, Sensor, BinaryInput,
    Circuit, DeviceCapabilities, OutputMode,
    Scene, SceneAction, SceneCall,
    UserAction, UserActionCondition, UserActionAction,
    TimedEvent, TimedEventTime, TimedEventRecurrence,
    SceneResponder, ResponderTrigger,
    UserState, UserStateCategory,
    EventSource,
)
from .events import (
    DssEvent, CallSceneEvent, UndoSceneEvent, ButtonClickEvent,
    StateChangeEvent, DeviceBinaryInputEvent, DeviceSensorEvent,
    ZoneSensorValueEvent, ZoneSensorErrorEvent, HighLevelEvent,
    DeviceEvent, ExecutionDeniedEvent, RunningEvent, ModelReadyEvent,
)
from .exceptions import (
    DssError, DssConnectionError, DssAuthError, DssTimeoutError,
    DssProtocolError, DssNotFoundError, DssTypeMismatchError,
)

__version__ = "0.1.0"
```

Konsumenten importieren ueber `pydigitalstrom`-Top-Level. Submodule sind interne Implementation.

## 3. Module-Reference

### 3.1 `auth.py` - App-Token + Session-Lifecycle

```python
@dataclass(frozen=True)
class AppToken:
    """Permanenter App-Token aus dSS Web-UI (Settings -> System Access -> Authorized Applications)."""
    value: str
    application_name: str  # informational only, nicht im Request

    @classmethod
    def from_env(cls, var_name: str = "DSS_APP_TOKEN", app_name: str = "HomeAssistant") -> AppToken:
        """Liest Token aus Environment-Variable. Wirft DssAuthError wenn nicht gesetzt."""

class SessionManager:
    """Verwaltet Session-Token-Lifecycle mit Auto-Re-Login.

    Nicht direkt instanziieren - wird vom DssClient verwendet."""

    def __init__(self, base_url: str, app_token: AppToken, *, http: httpx.AsyncClient) -> None: ...

    async def get_token(self) -> str:
        """Gibt aktuelles Session-Token zurueck, holt eines wenn keins da."""

    async def force_refresh(self) -> str:
        """Erzwingt neuen Login, gibt neues Token zurueck.
        Aufgerufen vom Client bei 401."""

    async def invalidate(self) -> None:
        """Markiert Token als ungueltig, naechster get_token() macht Login."""

    @property
    def login_count(self) -> int:
        """Diagnostik: Anzahl bisheriger Logins."""
```

### 3.2 `client.py` - HTTP-Wrapper + Auto-Retry

```python
class DssClient:
    """Async HTTP-Client gegen die dSS REST-API.

    Verwendung:
        async with DssClient(host="<dss-host>", app_token=token) as client:
            apartment = await fetch_apartment(client)
            ...
    """

    def __init__(
        self,
        host: str,
        app_token: AppToken,
        *,
        port: int = 8080,
        verify_ssl: bool = False,
        timeout: float = 10.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Args:
            host:        dSS-IP oder Hostname
            app_token:   Permanenter App-Token aus dSS Web-UI
            port:        HTTPS-Port (default 8080)
            verify_ssl:  False bei self-signed Cert (dSS-Standard)
            timeout:     HTTP-Timeout pro Request in Sekunden
            http_client: Optionaler eigener httpx-Client (z.B. fuer Tests)
        """

    async def __aenter__(self) -> DssClient: ...
    async def __aexit__(self, *exc: Any) -> None: ...

    @property
    def base_url(self) -> str: ...
    @property
    def host(self) -> str: ...
    @property
    def app_token(self) -> AppToken: ...
    @property
    def session(self) -> SessionManager: ...

    async def login(self) -> None:
        """Macht expliziten Login. Optional - wird sonst lazy beim ersten Call gemacht."""

    async def close(self) -> None:
        """Beendet HTTP-Client + invalidiert Session."""

    async def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
        unwrap_result: bool = True,
    ) -> Any:
        """GET-Request mit Auto-Re-Login bei 401.

        Args:
            path:           API-Pfad ohne BASE_URL, mit fuehrendem Slash, z.B. "/json/apartment/getStructure"
            params:         Query-Parameter (token wird automatisch hinzugefuegt)
            timeout:        Override fuer diesen Request, sonst Client-Default
            unwrap_result:  Wenn True (default), wird `data["result"]` zurueckgegeben, sonst die volle Response.

        Returns:
            JSON-Response. Wenn unwrap_result=True und ok=true, das "result"-Feld.

        Raises:
            DssConnectionError:  Netzwerk-Fehler
            DssAuthError:        Re-Login auch nicht erfolgreich
            DssProtocolError:    ok=false oder unerwartetes JSON-Format
            DssTimeoutError:     Timeout
        """

    async def get_raw(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """Wie get(), aber gibt rohe httpx.Response zurueck (z.B. fuer Long-Poll mit anderem Timeout-Handling)."""

    async def event_long_poll(
        self,
        subscription_id: int,
        timeout_ms: int = 30000,
    ) -> list[dict[str, Any]]:
        """Long-Poll-spezifischer Endpoint. Gibt geparste Events zurueck oder leere Liste bei Timeout.

        Verwendet eigenen HTTP-Timeout = timeout_ms/1000 + 10s.
        """
```

### 3.3 `apartment.py` - Apartment-Parser

```python
async def fetch_apartment(client: DssClient) -> Apartment:
    """Laedt komplette Apartment-Struktur und parsed in Apartment-Model.

    Aggregiert:
    - /json/apartment/getStructure  (Zonen, Devices, Groups, Clusters)
    - /json/apartment/getCircuits   (dSM-Meter)
    - /json/system/getDSID          (Apartment-ID)
    - /json/apartment/getName       (Apartment-Name)

    Returns: vollstaendiges, frozen Apartment-Object.
    """

async def fetch_apartment_name(client: DssClient) -> str: ...
async def fetch_apartment_circuits(client: DssClient) -> list[Circuit]: ...

def parse_apartment_structure(raw: dict[str, Any]) -> Apartment:
    """Pure parse-Funktion - testbar ohne Network.

    Args: getStructure-Response (apartment-key)
    """
```

### 3.4 `property.py` - Generischer Property-Tree-Walker

```python
class PropertyTreeWalker:
    """Walker fuer den dSS Property-Tree mit Type-Awareness."""

    def __init__(self, client: DssClient, *, max_depth: int = 8) -> None: ...

    async def get_children(self, path: str) -> list[PropertyChild]:
        """Liefert Children mit Name + Typ."""

    async def get_typed(self, path: str, prop_type: PropertyType | None = None) -> Any:
        """Liest Wert mit korrektem Getter.

        Wenn prop_type=None, wird zuerst getChildren auf Parent gerufen um Typ zu erkennen.
        Wenn bekannt, direkter Aufruf von getString / getInteger / getBoolean.

        Returns: str | int | bool | None
        """

    async def walk(
        self,
        root: str,
        *,
        max_depth: int | None = None,
        leaf_filter: Callable[[str], bool] | None = None,
    ) -> dict[str, Any]:
        """Rekursiver Walker. Liefert Nested-Dict mit Werten an Leaves.

        Args:
            root:        Start-Pfad
            max_depth:   Override fuer Walker-Tiefe
            leaf_filter: Optionaler Filter fuer Leaf-Pfade (z.B. nur "name" + "id" lesen)
        """

    async def set_typed(self, path: str, value: str | int | bool) -> None:
        """Schreibt Wert mit korrektem Setter basierend auf Python-Typ."""

@dataclass(frozen=True)
class PropertyChild:
    name: str
    type: PropertyType  # str-enum: "string" | "integer" | "boolean" | "none"

class PropertyType(str, Enum):
    STRING = "string"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    GROUP = "none"  # "none" = Subtree, kein Wert
```

### 3.5 `events.py` - Long-Poll Event-Subscription + Dispatcher

```python
EVENT_NAMES: tuple[str, ...] = (
    "callScene", "undoScene", "buttonClick", "stateChange",
    "deviceBinaryInputEvent", "deviceSensorEvent",
    "running", "model_ready", "highlevelevent",
    "DeviceEvent", "executionDenied",
    "zoneSensorValue", "zoneSensorError",
)
"""Default-Subscription-Set fuer EventStream."""

class EventStream:
    """Long-Poll Event-Subscription mit Auto-Reconnect.

    Verwendung:
        async with EventStream(client, subscription_id=4711) as stream:
            async for event in stream:
                print(event)
    """

    def __init__(
        self,
        client: DssClient,
        *,
        subscription_id: int | None = None,
        events: Iterable[str] = EVENT_NAMES,
        poll_timeout_ms: int = 30000,
        backoff_initial: float = 1.0,
        backoff_max: float = 30.0,
    ) -> None:
        """Args:
            subscription_id: Wenn None, wird ein zufaelliger int generiert.
            events:          Event-Namen die abonniert werden.
            poll_timeout_ms: Long-Poll-Timeout pro event/get-Call.
            backoff_*:       Exponential-Backoff-Parameter bei Reconnect.
        """

    async def __aenter__(self) -> EventStream: ...
    async def __aexit__(self, *exc: Any) -> None: ...

    def __aiter__(self) -> AsyncIterator[DssEvent]: ...

    async def subscribe_all(self) -> None:
        """Macht /json/event/subscribe fuer alle konfigurierten Events."""

    async def unsubscribe_all(self) -> None:
        """Cleanup."""

    async def stop(self) -> None:
        """Beendet die Stream-Loop graceful."""

    @property
    def subscription_id(self) -> int: ...
    @property
    def events(self) -> tuple[str, ...]: ...
    @property
    def is_running(self) -> bool: ...
    @property
    def event_count(self) -> int: ...
    @property
    def reconnect_count(self) -> int: ...


class EventDispatcher:
    """Higher-Level-Dispatcher: registriere Handler pro Event-Typ.

    Verwendung:
        dispatcher = EventDispatcher(stream)

        @dispatcher.on(CallSceneEvent)
        async def handle_scene(event: CallSceneEvent) -> None:
            ...

        await dispatcher.run()  # blockiert bis stop()
    """

    def __init__(self, stream: EventStream) -> None: ...

    def on(self, event_type: type[DssEvent]) -> Callable[[Handler], Handler]:
        """Decorator: registriere async-Handler fuer Event-Typ.

        Mehrere Handler pro Typ werden parallel aufgerufen.
        """

    def off(self, event_type: type[DssEvent], handler: Handler) -> None:
        """Entferne Handler."""

    async def run(self) -> None:
        """Starte Stream + dispatche jedes Event an passende Handler.
        Blockiert bis stop() oder Stream-Ende."""

    async def stop(self) -> None: ...

# Type-Alias
Handler = Callable[[DssEvent], Awaitable[None]]
```

### 3.6 `models.py`

Alle Datenklassen, siehe Sektion 4 fuer Felder.

## 4. Object-Model (Dataclasses)

Alle Models sind `@dataclass(frozen=True)` - immutable. Pro Dataclass:
- **Quelle:** welcher dSS-Endpoint liefert die Daten
- **Felder:** Name, Python-Typ, Source-Field im JSON, Beispielwert, Notes
- **Helfer-Properties / Methods**

### 4.1 `Apartment`

**Quelle:** `/json/apartment/getStructure` (+ `getName`, `getDSID`, `getCircuits`)

```python
@dataclass(frozen=True)
class Apartment:
    name: str                       # apartment.name aus getName, z.B. "DIGITAL"
    dsid: str                       # system/getDSID -> result.dSID
    dsuid: str                      # system/getDSID -> result.dSUID
    zones: tuple[Zone, ...]         # alle Zonen incl. System
    clusters: tuple[Cluster, ...]   # apartment.clusters[]
    circuits: tuple[Circuit, ...]   # apartment/getCircuits
```

**Methods:**
```python
def get_zone(self, zone_id: int) -> Zone | None: ...
def get_device(self, dsuid: str) -> Device | None:
    """Findet Device ueber alle Zonen anhand dSUID."""
def get_device_by_dsid(self, dsid: str) -> Device | None: ...
def find_zone_by_name(self, name: str) -> Zone | None: ...

@property
def user_zones(self) -> tuple[Zone, ...]:
    """Zonen ohne System-IDs (0, 14368, 65534)."""

@property
def all_devices(self) -> tuple[Device, ...]:
    """Flat-Liste aller Devices aus allen User-Zonen (deduped via dSUID)."""
```

### 4.2 `Zone`

**Quelle:** `apartment.zones[<i>]`

```python
@dataclass(frozen=True)
class Zone:
    zone_id: int                    # zones[].id
    name: str                       # zones[].name (kann "" bei System-Zonen sein)
    devices: tuple[Device, ...]     # zones[].devices[]
    groups: tuple[Group, ...]       # zones[].groups[]

    @property
    def is_system_zone(self) -> bool:
        """True wenn zone_id in (0, 14368, 65534) - das sind die in die Test-Box. Generischer ist Heuristik: name leer ODER id sehr klein/gross."""

    def get_group(self, group_id: int) -> Group | None: ...
    def get_device(self, dsuid: str) -> Device | None: ...

    @property
    def light_group(self) -> Group | None:
        """group_id == 1 (Standard-Yellow-Light)."""

    @property
    def shade_group(self) -> Group | None:
        """group_id == 2 (Standard-Grey-Shade/Cover)."""
```

### 4.3 `Cluster`

**Quelle:** `apartment.clusters[<i>]`

```python
@dataclass(frozen=True)
class Cluster:
    cluster_id: int                 # clusters[].id (die Test-Box: 16)
    name: str                       # clusters[].name (die Test-Box: "Windschutz Jalousien")
    color: int                      # clusters[].color
    application_type: int           # clusters[].applicationType (2 bei die Test-Box)
    is_present: bool                # clusters[].isPresent
    # weitere Felder je nach Box, abgeleitet aus Live-Discovery erweitern
```

### 4.4 `Group`

**Quelle:** `zones[<i>].groups[<j>]`

```python
@dataclass(frozen=True)
class Group:
    group_id: int                   # groups[].id
    name: str                       # groups[].name
    color: int                      # groups[].color
    application_type: int           # groups[].applicationType
    is_present: bool                # groups[].isPresent
    device_dsuids: tuple[str, ...]  # Wer ist im Group (geparst aus groups[].devices oder explicit)

    @property
    def is_light(self) -> bool:    # group_id == 1
    @property
    def is_shade(self) -> bool:    # group_id == 2
    @property
    def is_user_group(self) -> bool:  # ID > 9 typischerweise Custom-Group
```

### 4.5 `Device`

**Quelle:** `zones[<i>].devices[<j>]` (+ ggf. `/json/device/getInfo`)

```python
@dataclass(frozen=True)
class Device:
    dsuid: str                      # devices[].dSUID - primary key
    dsid: str                       # devices[].id - legacy ID
    display_id: str                 # devices[].DisplayID
    name: str                       # devices[].name
    zone_id: int                    # parent zone
    
    gtin: str | None                # devices[].GTIN (Hersteller-Code)
    hw_info: str                    # devices[].HWInfo
    function_id: int                # devices[].functionID
    product_id: int                 # devices[].productID
    revision_id: int                # devices[].revisionID
    model: str | None               # devices[].model
    
    is_present: bool                # devices[].isPresent
    is_valid: bool                  # devices[].isValid
    last_discovered: str | None     # devices[].lastDiscovered
    first_seen: str | None          # devices[].firstSeen
    
    output_mode: OutputMode         # devices[].outputMode (Enum)
    output_channels: tuple[OutputChannel, ...]  # devices[].outputChannels
    
    button_input_mode: int | None   # devices[].buttonInputMode
    button_input_index: int | None
    button_id: int | None
    
    groups: tuple[int, ...]         # devices[].groups - in welchen Group-IDs Mitglied
    
    capabilities: DeviceCapabilities
    sensors: tuple[Sensor, ...]     # devices[].sensors
    binary_inputs: tuple[BinaryInput, ...]  # devices[].binaryInputs
    
    meter_dsid: str | None          # devices[].meterDSUID (Welcher Circuit/dSM)


@dataclass(frozen=True)
class DeviceCapabilities:
    has_output: bool
    has_input: bool
    has_sensors: bool
    has_binary_inputs: bool
    has_output_angle: bool          # Jalousien-Tilt


class OutputMode(int, Enum):
    """devices[].outputMode int -> Semantik. Wert-Bedeutungen aus dSS-Doku."""
    DISABLED = 0
    SWITCHED = 16
    DIMMED = 22
    DIMMED_RGB = 35
    POS_CONTROL = 33     # Jalousie
    POS_CONTROL_TILT = 42
    # ... vollstaendig in dSS-Doku, beim Implementieren erweitern
    UNKNOWN = -1
    
    @classmethod
    def from_raw(cls, raw: int) -> OutputMode:
        try:
            return cls(raw)
        except ValueError:
            return cls.UNKNOWN


@dataclass(frozen=True)
class OutputChannel:
    channel_index: int              # outputChannels[].channelIndex
    channel_id: int                 # outputChannels[].channelId
    channel_name: str | None        # outputChannels[].channelName


@dataclass(frozen=True)
class Sensor:
    sensor_index: int               # sensors[].sensorIndex
    sensor_type: int                # sensors[].sensorType - siehe SensorType-Enum
    last_value: float | None        # sensors[].sensorValueFloat (parsed)
    last_value_ts: datetime | None  # sensors[].sensorValueTimestamp (parsed ISO)
    
    @property
    def sensor_type_enum(self) -> SensorType: ...


class SensorType(int, Enum):
    """sensorType -> Semantik. Wertliste aus dSS-Doku (in Discovery teilweise sichtbar)."""
    TEMP_ROOM = 9
    BRIGHTNESS_ROOM = 76
    OUTDOOR_TEMP = 77
    # ... weitere beim Implementieren erweitern
    UNKNOWN = -1


@dataclass(frozen=True)
class BinaryInput:
    input_index: int                # binaryInputs[].inputIndex
    input_type: int                 # binaryInputs[].inputType
    target_type: int                # binaryInputs[].targetType
    target_group: int               # binaryInputs[].targetGroup
    state: bool | None              # binaryInputs[].state
```

### 4.6 `Circuit` (dSM-Meter)

**Quelle:** `/json/apartment/getCircuits` -> `circuits[]`

```python
@dataclass(frozen=True)
class Circuit:
    name: str                       # circuits[].name (z.B. "dSM Meter FI20")
    dsid: str                       # circuits[].dsid
    dsuid: str                      # circuits[].dSUID
    hw_version: str | None          # circuits[].hwVersion
    sw_version: str | None          # circuits[].swVersion
    armSwVersion: str | None
    api_version: str | None
    is_present: bool                # circuits[].isPresent
    is_valid: bool                  # circuits[].isValid
    bus_member_type: int | None     # circuits[].busMemberType
```

### 4.7 `Scene`-Konzepte

Szenen sind im dSS keine Top-Level-Objekte, sondern Numbers-by-Convention pro Group. Wir modellieren sie als Helfer:

```python
@dataclass(frozen=True)
class SceneCall:
    """Einmaliger Szenen-Aufruf (fuer callScene/undoScene Befehle)."""
    zone_id: int
    group_id: int
    scene_id: int
    force: bool = False
    
    def to_params(self) -> dict[str, Any]: ...

class StandardScene(int, Enum):
    """Standard-Szenen-Nummern (group-agnostisch, gemeinsam fuer Light + Shade)."""
    PRESET_0 = 0       # AUS / Position 0
    PRESET_1 = 5       # EIN / Position 1
    PRESET_2 = 17
    PRESET_3 = 18
    PRESET_4 = 19
    DEEP_OFF = 68
    LOCAL_OFF = 13     # the test user's Aussensteckdosen "aus" entspricht 14, "ein" 13 - geraete-spezifisch
    LOCAL_ON = 14
    # vollstaendige Liste aus dSS-Doku - in v0.2 erweitern
```

### 4.8 `EventSource`

**Quelle:** Jedes Event hat `source`-Block.

```python
@dataclass(frozen=True)
class EventSource:
    """Universelles Origin-Objekt aller dSS-Events."""
    set_expr: str                   # source.set, z.B. ".zone(5).group(2)" oder "dsid(...)" oder ""
    zone_id: int                    # source.zoneID
    group_id: int                   # source.groupID
    dsid: str | None                # source.dsid - nur wenn isDevice=true
    is_apartment: bool              # source.isApartment
    is_group: bool                  # source.isGroup
    is_device: bool                 # source.isDevice
    
    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> EventSource: ...
    
    @property
    def kind(self) -> Literal["apartment", "group", "device", "unknown"]:
        """Convenience-Discriminator basierend auf isApartment/isGroup/isDevice."""
```

### 4.9 User-Action (`/usr/events/<id>`)

**Quelle:** Property-Tree `/usr/events/<id>` (gefiltert auf source=system-addon-user-defined-actions)

```python
@dataclass(frozen=True)
class UserAction:
    action_id: str                  # id (numerisch, als String gespeichert: "1471955518")
    name: str                       # name (z.B. "Markise Sitzplatz Sued schliessen")
    source: str                     # source-string (sollte "system-addon-user-defined-actions" sein)
    disabled: bool
    last_saved: datetime | None     # parsed aus epoch_ms-String
    last_executed: datetime | None  # parsed aus "YYYY-MM-DD HH:MM:SS"
    conditions: UserActionConditions
    actions: tuple[UserActionAction, ...]

    def is_user_defined(self) -> bool:
        return self.source == "system-addon-user-defined-actions"


@dataclass(frozen=True)
class UserActionConditions:
    enabled: bool                                       # conditions.enabled (bei timed-events)
    system_states: Mapping[str, str]                    # conditions.states - Key=statename, Value=erforderlicher Wert
    addon_states: Mapping[str, Mapping[str, int]]       # conditions.addon-states[addon-id][state-id] = int


@dataclass(frozen=True)
class UserActionAction:
    """Eine konkrete Aktion in einem User-Event."""
    action_type: UserActionType     # "device-scene", "zone-scene", "url", ...
    delay: int                      # Sekunden
    category: str                   # "manual" o.ae.
    
    # Type-spezifische Felder (nur die zum Type passenden sind != None)
    dsuid: str | None               # device-scene, device-blink
    zone: int | None                # zone-scene, undo-zone-scene, zone-blink
    group: int | None               # zone-scene, undo-zone-scene, zone-blink
    scene: int | None               # device-scene, zone-scene, undo-zone-scene
    force: bool | None              # device-scene, zone-scene, undo-zone-scene
    url: str | None                 # url
    statename: str | None           # change-state, change-addon-state
    state: str | None               # change-state, change-addon-state
    addon_id: str | None            # change-addon-state
    event: str | None               # custom-event (referenziert andere UserAction-ID)
    
    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> UserActionAction: ...


class UserActionType(str, Enum):
    DEVICE_SCENE = "device-scene"
    ZONE_SCENE = "zone-scene"
    URL = "url"
    CHANGE_ADDON_STATE = "change-addon-state"
    DEVICE_BLINK = "device-blink"
    CUSTOM_EVENT = "custom-event"
    UNDO_ZONE_SCENE = "undo-zone-scene"
    CHANGE_STATE = "change-state"
    ZONE_BLINK = "zone-blink"
```

### 4.10 `TimedEvent` (Zeitschaltuhr)

**Quelle:** `/scripts/system-addon-timed-events/entries/<id>`

```python
@dataclass(frozen=True)
class TimedEvent:
    entry_id: str                   # entries.<id> (z.B. "21" - Wecker)
    name: str
    scope: str                      # immer "system-addon-timed-events"
    conditions: TimedEventConditions
    time: TimedEventTime
    actions: tuple[UserActionAction, ...]  # Selbe Struktur wie UserAction-Action!
    delete_counter: int
    last_executed: datetime | None


@dataclass(frozen=True)
class TimedEventConditions:
    enabled: bool


@dataclass(frozen=True)
class TimedEventTime:
    time_base: TimeBase             # "daily", "weekly", "sunrise", "sunset", ...
    offset_seconds: int             # Sekunden ab Mitternacht / Sonnenauf-/untergang
    recurrence_base: str            # "weekly" typisch
    recurrence: TimedEventRecurrence


@dataclass(frozen=True)
class TimedEventRecurrence:
    weekdays: tuple[Weekday, ...]   # geparst aus {"0":"MO","1":"TU",...}


class TimeBase(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    SUNRISE = "sunrise"
    SUNSET = "sunset"
    # vollstaendig in dSS-Doku


class Weekday(str, Enum):
    MO = "MO"
    TU = "TU"
    WE = "WE"
    TH = "TH"
    FR = "FR"
    SA = "SA"
    SU = "SU"
```

### 4.11 `SceneResponder`

**Quelle:** `/scripts/system-addon-scene-responder/entries/<id>`

```python
@dataclass(frozen=True)
class SceneResponder:
    entry_id: str                   # entry-ID
    name: str
    scope: str                      # "system-addon-scene-responder"
    technical_role: str | None      # "system" oder None
    persistent_scope: bool
    delay: int
    conditions: SceneResponderConditions
    triggers: tuple[ResponderTrigger, ...]
    actions: tuple[UserActionAction, ...]
    singular_triggered: bool
    initial_triggered: bool
    last_executed: datetime | None
    
    @property
    def is_system_managed(self) -> bool:
        """True wenn entry_id-Prefix wie 'md_' (motion-detector) oder technical_role=='system'."""


@dataclass(frozen=True)
class SceneResponderConditions:
    enabled: bool


@dataclass(frozen=True)
class ResponderTrigger:
    """Eine Trigger-Bedingung in einem Scene-Responder."""
    trigger_type: ResponderTriggerType
    addon_id: str | None            # bei addon-state-change
    name: str | None                # state-ID oder name
    state: str | None               # Trigger-Wert
    # Weitere Felder je nach trigger_type


class ResponderTriggerType(str, Enum):
    ADDON_STATE_CHANGE = "addon-state-change"
    # weitere in v0.2 ergaenzen (zone-scene-call, button-press, etc.)
```

### 4.12 `UserState`

**Quelle:** `/scripts/system-addon-user-defined-states/...`

```python
@dataclass(frozen=True)
class UserState:
    state_id: str                   # numerische ID als String (z.B. "1561580731")
    name: str
    category: UserStateCategory
    current_value: int | str | None # akutell, falls verfuegbar
    values: tuple[UserStateValue, ...] | None  # moegliche Werte (bei Enum-States)
    
    # Kategorie-spezifische Felder, je nach Sub-Tree-Schema
    # exakte Felder werden bei Implementierung aus archive/scripts_addons.json abgeleitet


class UserStateCategory(str, Enum):
    DEVICE_SENSOR = "device-sensor-states"
    CUSTOM = "custom-states"
    COMBINED = "combined-states"
    TRIGGERED = "triggered-states"


@dataclass(frozen=True)
class UserStateValue:
    value: int
    label: str
```

## 5. Event-Modell

Jeder Event-Typ erbt von `DssEvent` und mappt die `properties`-Werte auf typed properties.

### 5.1 Basis

```python
@dataclass(frozen=True)
class DssEvent:
    """Basis aller dSS-Events."""
    name: str
    properties: Mapping[str, str]   # Roh-Properties (alle Werte als String, dSS-Convention)
    source: EventSource
    received_at: datetime
    
    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> DssEvent:
        """Factory: dispatched zu konkreter Subklasse anhand raw['name'].
        Wenn name unbekannt, gibt generisches DssEvent zurueck.
        """
```

### 5.2 Konkrete Event-Klassen

Pro Event-Typ ein Dataclass mit typed properties (Properties intern, Helper-Properties extrahieren Werte).

```python
@dataclass(frozen=True)
class CallSceneEvent(DssEvent):
    @property
    def zone_id(self) -> int:        # properties["zoneID"]
    @property
    def group_id(self) -> int:       # properties["groupID"]
    @property
    def scene_id(self) -> int:       # properties["sceneID"]
    @property
    def call_origin(self) -> int:    # properties["callOrigin"]
    @property
    def origin_dsuid(self) -> str:   # properties["originDSUID"]
    @property
    def origin_token(self) -> str:   # properties["originToken"]


@dataclass(frozen=True)
class UndoSceneEvent(DssEvent):
    @property
    def zone_id(self) -> int: ...
    @property
    def group_id(self) -> int: ...
    @property
    def call_origin(self) -> int: ...


@dataclass(frozen=True)
class ButtonClickEvent(DssEvent):
    @property
    def button_index(self) -> int:   # properties["buttonIndex"]
    @property
    def click_type(self) -> int:     # properties["clickType"]
    @property
    def dsid(self) -> str:           # source.dsid (NICHT properties!)
    @property
    def click_type_enum(self) -> ClickType: ...


class ClickType(int, Enum):
    SINGLE = 0
    DOUBLE = 1
    TRIPLE = 2
    HOLD = 3
    RELEASE = 4
    UNKNOWN = -1


@dataclass(frozen=True)
class StateChangeEvent(DssEvent):
    @property
    def state_name(self) -> str:     # properties["statename"]
    @property
    def state(self) -> str:          # properties["state"] ("active"/"inactive"/..)
    @property
    def value(self) -> str:          # properties["value"]
    @property
    def old_value(self) -> str:      # properties["oldvalue"]
    @property
    def call_origin(self) -> int | None: ...


@dataclass(frozen=True)
class DeviceBinaryInputEvent(DssEvent):
    @property
    def input_state(self) -> int: ...
    @property
    def input_index(self) -> int: ...
    @property
    def input_type(self) -> int: ...
    @property
    def dsid(self) -> str: ...       # aus source.dsid


@dataclass(frozen=True)
class DeviceSensorEvent(DssEvent):
    @property
    def sensor_index(self) -> int: ...
    @property
    def sensor_type(self) -> int: ...
    @property
    def sensor_value(self) -> int: ...
    @property
    def sensor_value_float(self) -> float: ...
    @property
    def dsid(self) -> str: ...


@dataclass(frozen=True)
class ZoneSensorValueEvent(DssEvent):
    @property
    def zone_id(self) -> int: ...
    @property
    def sensor_type(self) -> int: ...
    @property
    def sensor_value(self) -> int: ...
    @property
    def sensor_value_float(self) -> float: ...
    @property
    def origin_dsid(self) -> str: ...


@dataclass(frozen=True)
class ZoneSensorErrorEvent(DssEvent):
    @property
    def zone_id(self) -> int: ...
    @property
    def sensor_type(self) -> int: ...
    @property
    def last_value_ts(self) -> datetime | None: ...  # 1970 bei never-seen


@dataclass(frozen=True)
class HighLevelEvent(DssEvent):
    """User-Defined-Action wurde ausgefuehrt."""
    @property
    def action_id(self) -> str:      # properties["id"]
    @property
    def source_name(self) -> str:    # properties["source-name"]


@dataclass(frozen=True)
class DeviceEvent(DssEvent):
    """Generischer Device-Event - Felder variieren, raw properties verwenden."""


@dataclass(frozen=True)
class ExecutionDeniedEvent(DssEvent):
    """Call wurde durch Conditions abgelehnt."""


@dataclass(frozen=True)
class RunningEvent(DssEvent):
    """dSS-Boot/Restart-Marker - Library re-subscribed alle Events."""


@dataclass(frozen=True)
class ModelReadyEvent(DssEvent):
    """Modell wurde refreshed - Library invalidiert Apartment-Cache."""
```

### 5.3 Event-Factory-Mapping

```python
EVENT_CLASS_MAP: Mapping[str, type[DssEvent]] = {
    "callScene":               CallSceneEvent,
    "undoScene":               UndoSceneEvent,
    "buttonClick":             ButtonClickEvent,
    "stateChange":             StateChangeEvent,
    "deviceBinaryInputEvent":  DeviceBinaryInputEvent,
    "deviceSensorEvent":       DeviceSensorEvent,
    "zoneSensorValue":         ZoneSensorValueEvent,
    "zoneSensorError":         ZoneSensorErrorEvent,
    "highlevelevent":          HighLevelEvent,
    "DeviceEvent":             DeviceEvent,
    "executionDenied":         ExecutionDeniedEvent,
    "running":                 RunningEvent,
    "model_ready":             ModelReadyEvent,
}

def parse_event(raw: dict[str, Any]) -> DssEvent:
    """Factory: nimmt rohes dSS-Event-Dict, gibt typed DssEvent zurueck."""
    cls = EVENT_CLASS_MAP.get(raw.get("name", ""), DssEvent)
    return cls.from_raw(raw)
```

## 6. Addon-Module

Jedes System-Addon kapselt sein CRUD-Pattern in einer eigenen Klasse. Universelles Interface:

```python
class AddonBase:
    """Basis fuer alle Addon-Module."""

    def __init__(self, client: DssClient) -> None: ...

    @property
    def addon_name(self) -> str:
        """z.B. 'system-addon-timed-events'."""
        ...

    @property
    def config_event_name(self) -> str:
        """z.B. 'system-addon-timed-events.config'."""
        ...

    async def _raise_config(self, parameter: str) -> None:
        """Internal: feuert /json/event/raise mit dem Addon-Config-Event."""
```

### 6.1 `addons/timed_events.py`

```python
class TimedEventsAddon(AddonBase):
    """Zugriff auf /scripts/system-addon-timed-events/entries/*."""

    async def list_entries(self) -> list[TimedEvent]:
        """Liefert alle Zeitschaltuhr-Eintraege."""

    async def get_entry(self, entry_id: str) -> TimedEvent | None: ...

    async def save_entry(self, entry: TimedEvent) -> None:
        """Create oder Update via event/raise system-addon-timed-events.config actions=save."""

    async def delete_entry(self, entry_id: str) -> None:
        """Delete via actions=delete (zu verifizieren in Discovery)."""

    async def trigger_entry(self, entry_id: str) -> None:
        """Direkt ausloesen via actions=execute (zu verifizieren)."""
```

### 6.2 `addons/scene_responder.py`

```python
class SceneResponderAddon(AddonBase):
    """Zugriff auf /scripts/system-addon-scene-responder/entries/*."""

    async def list_entries(self, *, include_system: bool = False) -> list[SceneResponder]:
        """Default: nur User-editable Entries. include_system=True liefert auch md_*-System-Responder."""

    async def get_entry(self, entry_id: str) -> SceneResponder | None: ...

    async def save_entry(self, entry: SceneResponder) -> None: ...
    async def delete_entry(self, entry_id: str) -> None: ...

    async def enable(self, entry_id: str) -> None:
        """Convenience: setzt conditions.enabled=true ueber Save."""

    async def disable(self, entry_id: str) -> None: ...
```

### 6.3 `addons/user_actions.py`

```python
class UserActionsAddon(AddonBase):
    """Zugriff auf User-Actions unter /usr/events/<id>.

    NICHT unter /scripts/system-addon-user-defined-actions - dieser Pfad ist nur Container."""

    async def list_actions(self) -> list[UserAction]:
        """Liefert alle UserActions (gefiltert nach source=='system-addon-user-defined-actions')."""

    async def get_action(self, action_id: str) -> UserAction | None: ...

    async def trigger_by_name(self, name: str) -> None:
        """event/raise highlevelevent actionName=<name>."""

    async def trigger_by_id(self, action_id: str) -> None:
        """Loest via Reverse-Lookup: erst Name aus action_id finden, dann trigger_by_name."""

    async def save_action(self, action: UserAction) -> None: ...
    async def delete_action(self, action_id: str) -> None: ...
```

### 6.4 `addons/user_states.py`

```python
class UserStatesAddon(AddonBase):
    async def list_states(
        self,
        category: UserStateCategory | None = None,
    ) -> list[UserState]:
        """Filterbar nach Kategorie."""

    async def get_state(self, state_id: str) -> UserState | None: ...

    async def set_state_value(self, state_id: str, value: int | str) -> None:
        """change-addon-state via event/raise."""
```

### 6.5 Weitere Addons (v0.3+)

- `addons/heating.py` - `system-addon-heating-controller`
- `addons/ventilation.py` - `system-addon-ventilation-controller`
- `addons/presence_simulator.py`
- `addons/event_mailer.py` (Email-Send-Service)
- `addons/motion_detector.py`

Pattern bleibt: AddonBase mit `addon_name`, `config_event_name`, CRUD-Methoden auf Domain-Objekte.

## 7. Exceptions

```python
class DssError(Exception):
    """Basis aller pydigitalstrom-Exceptions."""

class DssConnectionError(DssError):
    """Network-/Connect-Fehler (kein Response)."""

class DssTimeoutError(DssError):
    """Request-Timeout."""

class DssAuthError(DssError):
    """Login fehlgeschlagen oder Token revoked."""
    
    def __init__(self, message: str, *, app_token_invalid: bool = False) -> None: ...

class DssProtocolError(DssError):
    """Unerwartetes JSON-Format oder ok=false ohne handle-bare message."""

class DssNotFoundError(DssError):
    """Resource (Zone, Device, Action, ...) nicht gefunden."""

class DssTypeMismatchError(DssError):
    """property/getString auf int-Field o.ae. - typisch wenn vorher nicht via getChildren der Typ geklaert wurde."""

class DssSubscriptionError(DssError):
    """Event-Subscribe/Unsubscribe fehlgeschlagen."""
```

## 8. CLI-Tool: `pydigitalstrom-cli`

Standalone-Tool fuer manuelle Inspektion + Smoke-Tests. Installiert via `pyproject.toml` als Console-Script.

### Commands

```
pydigitalstrom-cli --host <dss-host> --token <APP_TOKEN> <COMMAND> [ARGS]

Commands:
  apartment              Apartment-Struktur dumpen (Zonen, Devices)
  list-zones             Nur Zonen-Liste
  list-devices [--zone N] Devices-Liste, optional gefiltert
  list-circuits          dSM-Meter
  
  call-scene <zone> <group> <scene>          Szene aufrufen
  undo-scene <zone> <group>
  
  list-user-actions      Alle User-Defined-Actions
  show-user-action <id>
  trigger-action <name>  User-Action via Name ausloesen
  
  list-timed-events      Zeitschaltuhr-Eintraege
  show-timed-event <id>
  
  list-scene-responders [--include-system]
  show-scene-responder <id>
  
  watch-events [--seconds N] [--filter callScene,buttonClick]
                         Long-Poll-Stream live anzeigen
  
  property-tree <path> [--depth N] [--format json|tree]
                         Property-Tree-Subtree dumpen
  
  raw-get <path> [--param key=value ...]
                         Direkter GET an /json/<path>, JSON-Response
  
  health-check           Login + getDSID, exit 0 wenn OK
  
  diagnostics            Voller Health-Report: Login, Apartment-Counts, Event-Subscribe-Test
```

### Beispiele

```bash
# Apartment-Snapshot
pydigitalstrom-cli apartment --format json > apartment_snapshot.json

# Live-Events watchen
pydigitalstrom-cli watch-events --filter callScene,buttonClick --seconds 300

# Zeitschaltuhr-Wecker zeigen
pydigitalstrom-cli show-timed-event 21

# Wecker manuell triggern (zur Tag-Zeit testen)
pydigitalstrom-cli trigger-action "Wecker ausfuehren"
```

CLI nutzt intern `argparse` + `asyncio.run()`. Output strukturiert (JSON oder readable Tabelle). Logging via `--verbose` Flag.

## 9. Test-Strategie

### 9.1 Unit-Tests (pytest + respx)

Pro Modul ein Test-File:

```
tests/
├── unit/
│   ├── test_auth.py
│   ├── test_client.py
│   ├── test_apartment.py
│   ├── test_events.py
│   ├── test_property.py
│   ├── test_models.py
│   └── addons/
│       ├── test_timed_events.py
│       ├── test_scene_responder.py
│       ├── test_user_actions.py
│       └── test_user_states.py
├── integration/
│   ├── test_live_dss.py        # Nur mit --run-live Flag, gegen echte Box
│   └── conftest.py
└── fixtures/
    ├── apartment_structure.json    ← aus archive/ kopiert (gekuerzt/redacted)
    ├── events/                     ← extrahierte Sample-Events
    │   ├── callScene.json
    │   ├── buttonClick.json
    │   └── ...
    ├── addons/
    │   ├── timed_events_entry_21.json
    │   ├── scene_responder_entry_17.json
    │   └── ...
    └── property_tree_subtree.json
```

**Fixture-Source:** Die `archive/`-Dateien aus dem Discovery-Run werden als Test-Quelle verwendet:
- Sensitive Daten (echte DSUIDs, Namen) anonymisiert oder gegen Schema-Validity getestet
- Wenn keine Anonymisierung noetig: direkt das archive-File symlinken / kopieren

**respx fuer Network-Mocking:**

```python
import respx
from pydigitalstrom import DssClient, AppToken

@respx.mock
async def test_apartment_load():
    respx.get("https://<dss-host>:8080/json/system/loginApplication").respond(
        json={"result": {"token": "mocked"}, "ok": True}
    )
    respx.get("https://<dss-host>:8080/json/apartment/getStructure").respond(
        json=load_fixture("apartment_structure.json")
    )
    
    async with DssClient("<dss-host>", AppToken("test", "test")) as client:
        apt = await fetch_apartment(client)
        assert len(apt.zones) == 20
        assert apt.get_zone(5).name == "Arbeiten"
```

### 9.2 Integration-Tests (Live)

```python
# nur ausgefuehrt mit --run-live + ENV vars gesetzt
@pytest.mark.live
async def test_live_login(live_client: DssClient):
    await live_client.login()
    assert live_client.session._session_token is not None
```

Gated via:
- `pytest --run-live` flag
- ENV `DSS_TEST_HOST` + `DSS_TEST_TOKEN` muessen gesetzt sein
- Default: skip

### 9.3 Coverage-Ziele

- v0.1: >= 75% Line-Coverage
- v0.5: >= 85%
- v1.0: >= 90%

Branch-Coverage fuer kritische Pfade (Auth-Retry, Event-Reconnect).

### 9.4 Type-Checks

- `mypy --strict pydigitalstrom`
- `ruff check pydigitalstrom`
- Beides in CI als Pflicht-Gate

## 10. Konventionen

### 10.1 Async-Konvention

- **Alle Public-Methods sind async** - keine sync-Varianten
- Helper-Funktionen (`parse_*`, `_to_*`) sind sync wenn pure transform
- Generatoren via `AsyncIterator`/`AsyncIterable`

### 10.2 Return-Konvention

- **Niemals raw dicts** aus Public-API zurueckgeben
- Bei "nicht gefunden": `Optional[Model]` (None) - NICHT Exception
- Bei API-Fehlern: passende `DssError`-Subklasse raisen
- Mutationen (save_*, set_*) sind void (`None`), Erfolg = kein Exception

### 10.3 Naming

- Klassen: `PascalCase`
- Methods/Functions: `snake_case`
- Async-Methods nicht mit `async_`-Prefix versehen (Python-Konvention)
- Booleans als `is_*` / `has_*` (kein `flag` o.ae.)

### 10.4 Logging

Pflicht laut Enterprise-Rules:

```python
import logging
_LOGGER = logging.getLogger(__name__)

# Modul-Prefix in jeder Message:
_LOGGER.info(f"[pydss.client] Login successful (session #{count})")
_LOGGER.debug(f"[pydss.events] event/get returned {len(events)} events")
_LOGGER.error(f"[pydss.auth] App-Token rejected: {error}")
```

Prefix-Konvention: `[pydss]` (top-level), `[pydss.<module>]` (Submodule).

### 10.5 Type-Hints

- Python 3.10+ Syntax: `Model | None`, `list[X]`, `dict[K, V]`
- Library-Public-Types unter `pydigitalstrom.types` re-exportiert
- Keine `Any` ausser bei JSON-Parsing-Boundaries

### 10.6 Doc-Strings

Google-Style nach Enterprise-Rules:

```python
async def get(self, path: str, params: dict | None = None) -> Any:
    """GET-Request mit Auto-Re-Login.

    Args:
        path: API-Pfad mit fuehrendem Slash.
        params: Query-Parameter (token wird automatisch hinzugefuegt).

    Returns:
        JSON-Response-result-Feld.

    Raises:
        DssAuthError: Re-Login fehlgeschlagen.
        DssTimeoutError: Request-Timeout.
    """
```

## 11. Versions-Roadmap

**Strategischer Kontext (2026-05-29):** digitalSTROM wird in ~12 Monaten durch KNX abgeloest. Library-Scope ist entsprechend pragmatisch - kein Production-Push, kein PyPI/HACS-Release, keine v1.0. Ziel: gut genug fuer die Bridge-Phase + spaeter als KNX-Migrations-Tool verwendbar (Device-/State-Inventar auslesen).

### v0.1 - Foundation (1-2 Wochen)

Liefert die Grundbausteine, mit denen die HA-Integration arbeiten kann.

- `auth.py`: AppToken + SessionManager mit Auto-Re-Login
- `client.py`: DssClient mit get() + Auto-Retry
- `apartment.py`: fetch_apartment, parse_apartment_structure
- `models.py`: Apartment, Zone, Cluster, Group, Device, Circuit, Sensor, BinaryInput, OutputMode, EventSource (alle Core-Modelle)
- `events.py`: EventStream + EventDispatcher + alle Event-Klassen
- `property.py`: PropertyTreeWalker
- `exceptions.py`: vollstaendige Hierarchie
- CLI: `apartment`, `list-zones`, `list-devices`, `call-scene`, `watch-events`, `health-check`
- Tests: >= 70% Coverage (knapper als bei langfristigen Projekten)

**Akzeptanz-Kriterien:**
- HA-Integration kann gegen die Library v0.1 alle Zonen + Devices laden, Szenen aufrufen, Events empfangen
- Live-Smoke-Test gegen die Test-Box laeuft >= 1h stabil mit Reconnect

### v0.2 - Addons fuer the test user's Use-Cases (1 Woche)

Nur die Addons die the user in seinen ioBroker-Scripts aktiv nutzt:

- `addons/timed_events.py`: List + Get + Save (Wecker, Aussensteckdosen)
- `addons/user_actions.py`: List + Get + Trigger (Audio, Lichter, etc.)
- `addons/user_states.py`: List + Get + SetValue (Praesenz, Wochenende, Boiler)
- CLI: `list-timed-events`, `list-user-actions`, `trigger-action`, `list-user-states`

### Out-of-Scope (bewusst nicht gebaut)

- **v0.3+** entfaellt - Scene-Responder werden via dSS-Web weiter gepflegt (102 Eintraege, nicht in HA replizieren)
- **Heating/Ventilation/Presence-Simulator Addons** - werden in HA als Status-Read-Only abgebildet ueber Property-Tree-Walker, kein eigenes Addon-Modul
- **PyPI-Release / HACS / HA-Brand-PR** - bei 12-Monats-Lifetime nicht wirtschaftlich
- **Performance-Tuning ueber Funktional-Korrektheit hinaus** - reicht fuer the test user's Last (1 Apartment, 274 Devices)

### KNX-Migrations-Phase (~Q1 2027)

Library bleibt als **Inventar-Quelle** nuetzlich:
- `fetch_apartment()` → Geraete-Mapping fuer KNX-Setup
- `UserStatesAddon.list_states()` → State-Inventar zur Konvertierung
- `TimedEventsAddon.list_entries()` → Schedule-Migrations-Quelle
- Read-Pfad muss bis dahin stabil sein. Write-Pfad nicht mehr noetig wenn dSS abgeloest wird.

## Was diese Doku NICHT abdeckt

- **HA-Integration-Glue** (`custom_components/digitalstrom/`) - separate Spec in `ARCHITECTURE.md`
- **HA-Entity-Mapping** (Zone → Area, Device → Entity-Group, etc.) - Phase nach Library v0.1
- **dSS-Web-UI-Replikation** - User-Action-Editor, Schedule-Editor sind explizit Out-of-Scope (Phase 2 nach erfolgreicher Migration)

## Aenderungs-Policy

- Diese Doku wird bei jedem API-Change der Library aktualisiert
- Public-API-Changes brauchen Eintrag im CHANGELOG
- Breaking-Changes nur in Major-Version-Bumps (0.x kann breaking, 1.x+ nicht)
- Discovery-Phase-Findings die Modelle aendern → hier dokumentieren + CHANGE_LOG.md
