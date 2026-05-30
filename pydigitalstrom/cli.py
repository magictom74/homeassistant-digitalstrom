"""Command-line interface for pydigitalstrom.

Run smoke tests, inspect the apartment, watch events live. Useful both for
manual exploration and as integration-test driver.

Examples::

    pydigitalstrom-cli --host <DSS_IP_OR_HOST> --token $DSS_APP_TOKEN apartment
    pydigitalstrom-cli watch-events --seconds 60
    pydigitalstrom-cli call-scene 5 1 5
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import warnings
from typing import Any

from . import (
    AppToken,
    DssClient,
    EventStream,
    SceneCall,
    __version__,
    fetch_apartment,
)

_LOGGER = logging.getLogger("pydigitalstrom.cli")


def _silence_urllib3_warnings() -> None:
    """dSS uses a self-signed cert - mute the noise."""
    try:
        from urllib3.exceptions import InsecureRequestWarning
        warnings.filterwarnings("ignore", category=InsecureRequestWarning)
    except ImportError:
        pass


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pydigitalstrom-cli", description="pydigitalstrom command-line tool")
    p.add_argument("--host", default=os.environ.get("DSS_HOST"), help="dSS hostname or IP (or set env DSS_HOST)")
    p.add_argument("--port", type=int, default=int(os.environ.get("DSS_PORT", "8080")))
    p.add_argument(
        "--token",
        default=os.environ.get("DSS_APP_TOKEN"),
        help="App-Token (default: env DSS_APP_TOKEN)",
    )
    p.add_argument("--verbose", "-v", action="store_true", help="Enable DEBUG logging")
    p.add_argument("--version", action="version", version=f"pydigitalstrom {__version__}")

    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("health-check", help="Login + getDSID, exit 0 if OK")
    sub.add_parser("apartment", help="Print full apartment summary")
    sub.add_parser("list-zones", help="List zones (user-only by default)")
    p_zones = sub.add_parser("list-zones-all", help="List zones including system zones")
    p_zones.add_argument("--zone", type=int, help="Filter by zone id")

    p_dev = sub.add_parser("list-devices", help="List devices")
    p_dev.add_argument("--zone", type=int, help="Filter by zone id")

    sub.add_parser("list-circuits", help="List dSM-meters")

    p_call = sub.add_parser("call-scene", help="Call a scene")
    p_call.add_argument("zone", type=int)
    p_call.add_argument("group", type=int)
    p_call.add_argument("scene", type=int)
    p_call.add_argument("--force", action="store_true")

    p_undo = sub.add_parser("undo-scene", help="Undo last scene in a zone/group")
    p_undo.add_argument("zone", type=int)
    p_undo.add_argument("group", type=int)

    p_watch = sub.add_parser("watch-events", help="Stream events live")
    p_watch.add_argument("--seconds", type=int, default=60)
    p_watch.add_argument("--filter", help="Comma-separated event-name filter")

    p_raw = sub.add_parser("raw-get", help="Raw GET against /json/<path>")
    p_raw.add_argument("path", help="Path WITHOUT leading /json")
    p_raw.add_argument("--param", action="append", default=[], help="key=value (repeatable)")

    p_prop = sub.add_parser("property-tree", help="Dump a property-tree subtree as JSON")
    p_prop.add_argument("path")
    p_prop.add_argument("--depth", type=int, default=4)

    return p


async def _make_client(args: argparse.Namespace) -> DssClient:
    if not args.token:
        raise SystemExit("[pydss.cli] No app-token (use --token or env DSS_APP_TOKEN)")
    token = AppToken(value=args.token, application_name="pydigitalstrom-cli")
    return DssClient(host=args.host, port=args.port, app_token=token)


async def cmd_health_check(args: argparse.Namespace) -> int:
    async with await _make_client(args) as client:
        await client.login()
        info = await client.get("/json/system/getDSID")
        print(f"OK: dSID={info.get('dSID')} dSUID={info.get('dSUID')}")
    return 0


async def cmd_apartment(args: argparse.Namespace) -> int:
    async with await _make_client(args) as client:
        apt = await fetch_apartment(client)
    print(f"Apartment: {apt.name!r}  dsid={apt.dsid}  dsuid={apt.dsuid}")
    print(f"  Zones:    {len(apt.zones)} total, {len(apt.user_zones)} user")
    print(f"  Devices:  {len(apt.all_devices)} (deduped across user zones)")
    print(f"  Clusters: {len(apt.clusters)}")
    print(f"  Circuits: {len(apt.circuits)}")
    print()
    print("User zones:")
    for z in apt.user_zones:
        n_dev = len(z.devices)
        n_grp = len(z.groups)
        print(f"  [{z.zone_id:5}] {z.name!r:30}  devices={n_dev:3}  groups={n_grp}")
    return 0


async def cmd_list_zones(args: argparse.Namespace, include_system: bool = False) -> int:
    async with await _make_client(args) as client:
        apt = await fetch_apartment(client)
    zones = apt.zones if include_system else apt.user_zones
    for z in zones:
        sys_marker = "  [SYSTEM]" if z.is_system_zone else ""
        print(f"{z.zone_id:5}  {z.name!r:30}  devices={len(z.devices):3}{sys_marker}")
    return 0


async def cmd_list_devices(args: argparse.Namespace) -> int:
    async with await _make_client(args) as client:
        apt = await fetch_apartment(client)
    devices = apt.all_devices
    if args.zone is not None:
        zone = apt.get_zone(args.zone)
        if zone is None:
            print(f"Zone {args.zone} not found", file=sys.stderr)
            return 1
        devices = zone.devices
    for d in devices:
        print(f"{d.dsuid}  zone={d.zone_id:3}  mode={d.output_mode.name:18}  {d.name!r}")
    print(f"\nTotal: {len(devices)}")
    return 0


async def cmd_list_circuits(args: argparse.Namespace) -> int:
    async with await _make_client(args) as client:
        apt = await fetch_apartment(client)
    for c in apt.circuits:
        print(f"{c.dsuid}  hw={c.hw_version}  sw={c.sw_version}  {c.name!r}")
    return 0


async def cmd_call_scene(args: argparse.Namespace) -> int:
    call = SceneCall(zone_id=args.zone, group_id=args.group, scene_id=args.scene, force=args.force)
    async with await _make_client(args) as client:
        await client.get("/json/zone/callScene", params=call.to_params())
    print(f"OK: callScene zone={args.zone} group={args.group} scene={args.scene}")
    return 0


async def cmd_undo_scene(args: argparse.Namespace) -> int:
    async with await _make_client(args) as client:
        await client.get("/json/zone/undoScene", params={"id": args.zone, "groupID": args.group})
    print(f"OK: undoScene zone={args.zone} group={args.group}")
    return 0


async def cmd_watch_events(args: argparse.Namespace) -> int:
    name_filter: set[str] | None = None
    if args.filter:
        name_filter = {n.strip() for n in args.filter.split(",") if n.strip()}

    async with await _make_client(args) as client:
        async with EventStream(client) as stream:
            end_at = asyncio.get_event_loop().time() + args.seconds
            print(f"Watching events for {args.seconds}s "
                  f"(filter={sorted(name_filter) if name_filter else 'all'})...")
            async for event in stream:
                if name_filter is not None and event.name not in name_filter:
                    continue
                payload = {
                    "name": event.name,
                    "source": event.source.set_expr,
                    "props": dict(event.properties),
                }
                print(json.dumps(payload, ensure_ascii=False))
                if asyncio.get_event_loop().time() >= end_at:
                    break
    return 0


async def cmd_raw_get(args: argparse.Namespace) -> int:
    params: dict[str, Any] = {}
    for kv in args.param:
        if "=" not in kv:
            print(f"Bad --param (expected key=value): {kv}", file=sys.stderr)
            return 2
        k, v = kv.split("=", 1)
        params[k] = v
    path = args.path if args.path.startswith("/") else f"/json/{args.path}"
    async with await _make_client(args) as client:
        result = await client.get(path, params=params, unwrap_result=False)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


async def cmd_property_tree(args: argparse.Namespace) -> int:
    from .property import PropertyTreeWalker
    async with await _make_client(args) as client:
        walker = PropertyTreeWalker(client, max_depth=args.depth)
        tree = await walker.walk(args.path, max_depth=args.depth)
    print(json.dumps(tree, ensure_ascii=False, indent=2))
    return 0


COMMANDS = {
    "health-check": cmd_health_check,
    "apartment": cmd_apartment,
    "list-zones": lambda args: cmd_list_zones(args, include_system=False),
    "list-zones-all": lambda args: cmd_list_zones(args, include_system=True),
    "list-devices": cmd_list_devices,
    "list-circuits": cmd_list_circuits,
    "call-scene": cmd_call_scene,
    "undo-scene": cmd_undo_scene,
    "watch-events": cmd_watch_events,
    "raw-get": cmd_raw_get,
    "property-tree": cmd_property_tree,
}


def main(argv: list[str] | None = None) -> int:
    _silence_urllib3_warnings()
    parser = _build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    # Quiet httpx unless --verbose
    if not args.verbose:
        logging.getLogger("httpx").setLevel(logging.WARNING)

    handler = COMMANDS.get(args.command)
    if handler is None:
        parser.print_help()
        return 2

    try:
        return asyncio.run(handler(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
