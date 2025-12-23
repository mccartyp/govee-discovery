from __future__ import annotations

import argparse
import asyncio
import json
import sys

from . import __version__
from .discovery import run_listener, run_scan
from .interrogate import interrogate_all
from .store import RegistryStore


def add_common_db_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--db", default="./govee_registry.sqlite", help="SQLite registry DB path.")
    p.add_argument("--bind-ip", default="", help="Local IPv4 to bind (recommended on multi-homed hosts).")


def cmd_scan(args: argparse.Namespace) -> int:
    store = RegistryStore(args.db)
    try:
        async def _main() -> None:
            await run_scan(
                store=store,
                bind_ip=args.bind_ip,
                resolve_mac=args.resolve_mac,
                verbose=args.verbose,
                scan_repeat=args.scan_repeat,
                scan_interval=args.scan_interval,
            )

            if args.duration == 0:
                while True:
                    await asyncio.sleep(3600)
            else:
                await asyncio.sleep(args.duration)

        asyncio.run(_main())
        return 0
    finally:
        store.close()


def cmd_listen(args: argparse.Namespace) -> int:
    store = RegistryStore(args.db)
    try:
        async def _main() -> None:
            await run_listener(store=store, bind_ip=args.bind_ip, resolve_mac=args.resolve_mac, verbose=args.verbose)
            if args.duration == 0:
                while True:
                    await asyncio.sleep(3600)
            else:
                await asyncio.sleep(args.duration)

        asyncio.run(_main())
        return 0
    finally:
        store.close()


def cmd_dump(args: argparse.Namespace) -> int:
    store = RegistryStore(args.db)
    try:
        if args.kind == "devices":
            data = store.dump_devices()
        elif args.kind == "events":
            data = store.dump_scan_events(limit=args.limit, since_ms=args.since_ms)
        elif args.kind == "interrogations":
            data = store.dump_interrogations(limit=args.limit, since_ms=args.since_ms)
        elif args.kind == "kv":
            data = store.dump_kv(device_id=args.device_id, key_prefix=args.key_prefix, limit=args.limit)
        else:
            raise ValueError(f"unknown dump kind: {args.kind}")

        if args.pretty:
            json.dump(data, sys.stdout, indent=2, ensure_ascii=False)
        else:
            json.dump(data, sys.stdout, separators=(",", ":"), ensure_ascii=False)
        sys.stdout.write("\n")
        return 0
    finally:
        store.close()


def cmd_interrogate(args: argparse.Namespace) -> int:
    store = RegistryStore(args.db)
    try:
        interrogate_all(
            store=store,
            bind_ip=args.bind_ip,
            timeout_s=args.timeout,
            verbose=args.verbose,
            enrich=not args.no_enrich,
            only_ips=args.only_ip,
        )
        return 0
    finally:
        store.close()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="govee-discovery", description="Govee LAN discovery + registry tools.")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    sub = p.add_subparsers(dest="cmd", required=True)

    # scan
    ps = sub.add_parser("scan", help="Send multicast scan request(s) and listen for responses.")
    add_common_db_args(ps)
    ps.add_argument("--duration", type=int, default=15, help="Seconds to keep listening after scan; 0=forever.")
    ps.add_argument("--scan-repeat", type=int, default=3, help="Number of scan request packets to send.")
    ps.add_argument("--scan-interval", type=float, default=1.0, help="Seconds between scan packets.")
    ps.add_argument("--resolve-mac", action="store_true", help="Best-effort MAC via `ip neigh`.")
    ps.add_argument("--verbose", action="store_true", help="Print per-device scan logs.")
    ps.set_defaults(func=cmd_scan)

    # listen
    pl = sub.add_parser("listen", help="Listen only on UDP/4002 (no scan request).")
    add_common_db_args(pl)
    pl.add_argument("--duration", type=int, default=0, help="Seconds to listen; 0=forever.")
    pl.add_argument("--resolve-mac", action="store_true")
    pl.add_argument("--verbose", action="store_true")
    pl.set_defaults(func=cmd_listen)

    # dump
    pd = sub.add_parser("dump", help="Dump JSON entries from the SQLite registry.")
    add_common_db_args(pd)
    pd.add_argument("kind", choices=["devices", "events", "interrogations", "kv"], help="What to dump.")
    pd.add_argument("--limit", type=int, default=2000, help="Max rows (events/interrogations/kv).")
    pd.add_argument("--since-ms", type=int, default=None, help="Filter to rows >= this epoch ms (events/interrogations).")
    pd.add_argument("--pretty", action="store_true", help="Pretty-print JSON.")
    pd.add_argument("--device-id", default=None, help="Restrict KV dump to this device_id (kv only).")
    pd.add_argument("--key-prefix", default=None, help="Restrict KV dump keys to this prefix (kv only).")
    pd.set_defaults(func=cmd_dump)

    # interrogate
    pi = sub.add_parser("interrogate", help="Interrogate discovered devices to enrich the registry (devStatus).")
    add_common_db_args(pi)
    pi.add_argument("--timeout", type=float, default=2.0, help="UDP receive timeout (seconds).")
    pi.add_argument("--only-ip", action="append", default=None, help="Restrict to a specific device IP (repeatable).")
    pi.add_argument("--no-enrich", action="store_true", help="Do not normalize status fields into device_kv.")
    pi.add_argument("--verbose", action="store_true")
    pi.set_defaults(func=cmd_interrogate)

    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    rc = args.func(args)
    raise SystemExit(rc)
