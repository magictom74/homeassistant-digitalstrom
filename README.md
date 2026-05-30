# pydigitalstrom

Async Python library for the [digitalSTROM](https://www.digitalstrom.com/) dSS
REST API. Provides a fully typed, HA-agnostic interface to apartment
inventory, real-time events, circuit power monitoring, and all four
user-facing system-addons (Timed-Events, User-Actions, Scene-Responder,
User-States) with full CRUD support.

Reverse-engineered against dSS v1.54.0 via DevTools captures and live
verification. Designed as the back-end library for a Home Assistant
custom integration but usable from any Python codebase.

**Status:** Alpha. The public API may still change before 1.0. CRUD-paths
of every addon have been live-verified end-to-end against a real dSS box,
but the library has not yet been published to PyPI.

## Features

- **Apartment inventory** — fully parsed `Apartment / Zone / Cluster /
  Group / Device / Sensor / BinaryInput / Circuit` dataclasses, frozen and
  immutable.
- **Real-time events** via long-poll subscription. Typed event classes for
  all 13 event types observed in the wild (`CallSceneEvent`,
  `ButtonClickEvent`, `StateChangeEvent`, `ZoneSensorValueEvent`,
  `HighLevelEvent`, ...). Auto-reconnect, exponential backoff,
  re-subscribe on dSS restart.
- **Circuit power monitoring** — per-dSM watts + energy meter + state
  classification (`NORMAL` / `POWERING_UP` / `OFFLINE` / `UNREACHABLE` /
  `VIRTUAL`). Empirically verified outage detection rules.
- **Alarm framework** — rule-based alarm engine with built-in rules:
  `CircuitOutageRule`, `MotionWhileAbsentRule`, `ZoneTemperatureLowRule`,
  `SensorOutageRule`, `UserStateAlarmRule`. Bring your own
  `AlarmRule` for anything else.
- **System-addon CRUD** — full Create / Read / Update / Delete:
  - `TimedEventsAddon` (Zeitschaltuhr)
  - `UserActionsAddon` + trigger-by-name / trigger-by-id
  - `SceneResponderAddon` + enable/disable convenience
  - `UserStatesAddon` covering all four user-creatable sub-categories
    (`custom-states`, `combined-states`, `triggered-states`,
    `zone-sensor-states`)
- **CLI tool** — `pydigitalstrom-cli` for quick inspection
  (`health-check`, `apartment`, `list-zones`, `watch-events`,
  `call-scene`, `property-tree`, ...).
- **Auto re-login** on session-token expiry without callers having to
  care.

## Installation

```bash
# Library
pip install -e .

# Library + CLI extras
pip install -e ".[cli]"

# Library + development tooling
pip install -e ".[dev]"
```

Requires Python 3.10+ and `httpx >= 0.27`.

## Authentication

Generate a persistent application token once via the dSS web UI:

1. dSS web → `Settings → System Access → Authorized Applications`
2. Create a new application token (any name) — the dSS returns a 64-char
   hex string
3. Approve the application in the same dialog

Use that token from your code or as an environment variable.

## Quick start

```python
import asyncio
import os
from pydigitalstrom import AppToken, DssClient, fetch_apartment

async def main() -> None:
    token = AppToken(value=os.environ["DSS_APP_TOKEN"])
    async with DssClient(host=os.environ["DSS_HOST"], app_token=token) as client:
        apartment = await fetch_apartment(client)
        print(f"Apartment '{apartment.name}'")
        print(f"  zones:    {len(apartment.user_zones)}")
        print(f"  devices:  {len(apartment.all_devices)}")
        print(f"  circuits: {len(apartment.circuits)}")

asyncio.run(main())
```

### Live event stream

```python
from pydigitalstrom import EventStream, CallSceneEvent

async with EventStream(client) as stream:
    async for event in stream:
        if isinstance(event, CallSceneEvent):
            print(f"scene {event.scene_id} in zone {event.zone_id}/group {event.group_id}")
```

### Circuit power monitoring

```python
from pydigitalstrom import CircuitMonitor

monitor = CircuitMonitor(client, poll_interval_s=30)
for reading in await monitor.read_all():
    print(f"{reading.name:25} {reading.watts}W   state={reading.state.value}")
```

### Alarm engine

```python
from pydigitalstrom import AlarmEngine, CircuitOutageRule, UserStateAlarmRule

engine = AlarmEngine(client, state_names_to_poll=["water"])
engine.add_rule(CircuitOutageRule(ignore_dsuids=frozenset({"<dsuid-known-tot>"})))
engine.add_rule(UserStateAlarmRule(state_name="water", alarm_values=frozenset({"active"})))

async for alarm in engine.run():
    print(f"[{alarm.severity.value}] {alarm.title}: {alarm.description}")
```

### Addon CRUD example — user-state

```python
from pydigitalstrom import (
    UserStatesAddon, UserState, UserStateCategory,
)

addon = UserStatesAddon(client)

# Create a simple toggle state
new = UserState(
    state_id="",
    name="My new state",
    category=UserStateCategory.CUSTOM,
    current_value=None,
    set_name="On",
    reset_name="Off",
    show_on_phone=True,
)
new_id = await addon.save_state(new)
print(f"new state id: {new_id}")

# Update its value at runtime
await addon.set_state_value("My new state", "active")
```

## Command-line interface

```bash
export DSS_APP_TOKEN=<your-token>
export DSS_HOST=<your-dss-host-or-ip>

pydigitalstrom-cli health-check
pydigitalstrom-cli apartment
pydigitalstrom-cli list-zones
pydigitalstrom-cli list-devices --zone 5
pydigitalstrom-cli call-scene 5 1 5
pydigitalstrom-cli watch-events --seconds 60
pydigitalstrom-cli property-tree /scripts/system-addon-timed-events --depth 3
```

## Documentation

- [Library Design](docs/LIBRARY_DESIGN.md) — full reference of every
  module, dataclass, exception, and the public API contract.
- [Architecture](docs/ARCHITECTURE.md) — layers, event flow,
  reconnect strategy, test approach.
- [dSS REST-API Notes](docs/DSS_API_NOTES.md) — reverse-engineered API
  documentation with quirks, payload formats, and a list of dSS naming
  inconsistencies that bit us during reverse-engineering. Useful even if
  you don't use this library.

## Reverse-engineering highlights

A few dSS quirks the docs do not warn about:

- `circuit/getConsumption` requires the **short dSID** (24 chars), not the
  long dSUID (33 chars). Mapping: `dsid = dsuid[:11] + dsuid[20:33]`.
  Better to use `Circuit.dsid` directly from `getCircuits`.
- The save-event name is **`<addon>.config`** (dot) for every addon
  *except* `system-addon-user-defined-actions`, which uses
  `<addon>-config` (hyphen). The reply event follows the same
  convention.
- Combined-state conditions use `addonid` (camelCase, no hyphen) while
  the rest of the protocol uses `addon-id` (with hyphen).
- User-action save returns a Unix-timestamp id; scene-responder and
  timed-events save returns a sequential integer id; combined- /
  triggered- / zone-sensor-states all use Unix-timestamps. The new id
  is included in the `.saved` reply event's `properties.id` field.
- Scene-responder save actions include an index-keyed
  schema-placeholder field per action element
  (`{"0": [{"type":[]}, {"event":[]}, ...], "type": "...", ...}`).
  Same pattern in timed-events. The list of placeholder keys must match
  the actual keys emitted.
- `apartment/getStructure` reports the same device in multiple zones
  (e.g. broadcast zone 0 contains everything). `Apartment.all_devices`
  deduplicates.

See [docs/DSS_API_NOTES.md](docs/DSS_API_NOTES.md) for the full list
plus payload examples for every addon's save protocol.

## Development

```bash
# Lint + type-check
ruff check pydigitalstrom
mypy --strict pydigitalstrom

# Smoke-test against a real box via the CLI
export DSS_HOST=<your-dss>
export DSS_APP_TOKEN=<your-token>
pydigitalstrom-cli health-check
pydigitalstrom-cli apartment
pydigitalstrom-cli watch-events --seconds 30
```

A proper `pytest` suite with `respx`-mocked fixtures is on the roadmap
(see Contributing below).

## License

MIT — see [LICENSE](LICENSE).

## Contributing

Pull requests welcome. Things I'd love help with:

- Test suite with `respx`-mocked fixtures so CI can run without a real
  dSS box
- Devicesensor-state read support (currently the only sub-category
  without typed accessors)
- Multi-language string handling for state names
- Push to PyPI + GitHub Actions release workflow

When submitting a PR for a new addon save-format or response shape,
please include the DevTools capture (URL-decoded body + the saved-event
reply) so the protocol notes stay grounded in real evidence.
