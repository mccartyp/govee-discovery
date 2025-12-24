from __future__ import annotations

import argparse
import asyncio
import json
import sys

from . import __version__
from .control import (
    build_brightness_command,
    build_color_temp_command,
    build_color_payload,
    run_color_probe,
    build_turn_command,
    parse_color,
    send_control_command,
)
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
            target_ips=args.ip,
        )
        return 0
    finally:
        store.close()


def _resolve_target_ip(store: RegistryStore, ip: str | None, device_id: str | None) -> str:
    if ip:
        return ip
    if not device_id:
        raise ValueError("must supply --ip or --device-id")
    resolved = store.get_device_ip(device_id)
    if not resolved:
        raise ValueError(f"no IP found for device_id={device_id}")
    return resolved


def cmd_control(args: argparse.Namespace) -> int:
    store = RegistryStore(args.db)
    try:
        ip = _resolve_target_ip(store, args.ip, args.device_id)
        try:
            if args.action == "on":
                payload = build_turn_command(True)
            elif args.action == "off":
                payload = build_turn_command(False)
            elif args.action == "color":
                r, g, b = parse_color(args.color)
                payload = build_color_payload(
                    args.color_cmd,
                    color=(r, g, b),
                    kelvin=args.kelvin,
                    scale_max=args.color_scale,
                )
            elif args.action == "brightness":
                if not 0 <= args.value <= 100:
                    raise ValueError("brightness must be between 0 and 100")
                payload = build_brightness_command(args.value)
            elif args.action == "color-temp":
                if args.kelvin <= 0:
                    raise ValueError("color temperature must be positive")
                payload = build_color_temp_command(args.kelvin)
            elif args.action == "colorwc":
                color = parse_color(args.color) if args.color else None
                if color is None:
                    default_color = "warmwhite" if args.kelvin is not None and args.kelvin < 4000 else "white"
                    color = parse_color(default_color)
                payload = build_color_payload(
                    args.color_cmd,
                    color=color,
                    kelvin=args.kelvin,
                    scale_max=args.color_scale,
                )
            elif args.action == "color-probe":
                colors = args.color or ["red", "green", "blue"]
                kelvins = args.kelvin or [3000, 4000, 6500]
                parsed_colors: list[tuple[str, tuple[int, int, int]]] = []
                for color_name in colors:
                    try:
                        parsed_colors.append((color_name, parse_color(color_name)))
                    except ValueError as exc:
                        print(f"[control] invalid color {color_name!r}: {exc}", flush=True)
                        return 2

                if not kelvins:
                    print("[control] at least one kelvin value is required", flush=True)
                    return 2

                results = run_color_probe(
                    ip=ip,
                    colors=parsed_colors,
                    kelvin_values=kelvins,
                    include_no_kelvin=not args.require_kelvin,
                    bind_ip=args.bind_ip,
                    timeout_s=args.timeout,
                    stop_on_success=args.stop_on_success,
                    verbose=args.verbose,
                )

                if not results:
                    print("[control] no probe payloads were generated", flush=True)
                    return 1

                headers = ["cmd", "scale", "kelvin", "color", "status"]
                rows = []
                for res in results:
                    rows.append(
                        [
                            res.command,
                            str(res.scale_max),
                            str(res.kelvin) if res.kelvin is not None else "-",
                            res.color_name,
                            res.status(),
                        ]
                    )

                widths = [len(h) for h in headers]
                for row in rows:
                    for i, val in enumerate(row):
                        widths[i] = max(widths[i], len(val))

                def _fmt(row: list[str]) -> str:
                    return "  ".join(val.ljust(widths[i]) for i, val in enumerate(row))

                print(_fmt(headers))
                print(_fmt(["-" * w for w in widths]))
                for row in rows:
                    print(_fmt(row))

                return 0
            else:
                raise ValueError(f"unknown control action: {args.action}")
        except ValueError as exc:
            print(f"[control] invalid input: {exc}", flush=True)
            return 2

        if args.verbose:
            payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            print(f"[control] ip={ip} action={args.action} payload={payload_json}", flush=True)

        ok, resp, err = send_control_command(
            ip=ip,
            payload=payload,
            bind_ip=args.bind_ip,
            timeout_s=args.timeout,
            wait_response=not args.no_wait,
        )

        if not ok:
            print(f"[control] ip={ip} action={args.action} error={err or 'unknown'}", flush=True)
            return 1

        if resp is not None:
            if args.pretty:
                json.dump(resp, sys.stdout, indent=2, ensure_ascii=False)
            else:
                json.dump(resp, sys.stdout, separators=(",", ":"), ensure_ascii=False)
            sys.stdout.write("\n")
        elif err:
            print(f"[control] ip={ip} action={args.action} warning={err}", flush=True)
        elif args.verbose:
            print(f"[control] ip={ip} action={args.action} ok", flush=True)
        return 0
    finally:
        store.close()


def build_parser() -> argparse.ArgumentParser:
    examples = "\n".join(
        [
            "Examples:",
            "  govee-discovery scan --db ./govee_registry.sqlite --duration 20 --verbose",
            "  govee-discovery listen --db ./govee_registry.sqlite --duration 0 --verbose",
            "  govee-discovery interrogate --db ./govee_registry.sqlite --verbose",
            "  govee-discovery dump devices --db ./govee_registry.sqlite --pretty",
            "  govee-discovery control --ip 192.168.1.50 on",
            "  govee-discovery control --device-id ABCD1234 color red",
            "  govee-discovery control --ip 192.168.1.50 color #ff8800",
            "  govee-discovery control --ip 192.168.1.50 colorwc --kelvin 2700",
            "  govee-discovery control --ip 192.168.1.50 color red --color-cmd colorwc --kelvin 3200",
            "  govee-discovery control --ip 192.168.1.50 color red --color-cmd setColor --color-scale 100",
            "  govee-discovery control --ip 192.168.1.50 brightness 75",
            "  govee-discovery control --ip 192.168.1.50 color-temp 3500",
        ]
    )
    p = argparse.ArgumentParser(
        prog="govee-discovery",
        description="Govee LAN discovery + registry tools.",
        epilog=examples,
        formatter_class=argparse.RawTextHelpFormatter,
    )
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
    pi.add_argument("--ip", action="append", default=None, help="Interrogate explicit IP (repeatable).")
    pi.add_argument("--only-ip", action="append", default=None, help="Restrict to a specific device IP (repeatable).")
    pi.add_argument("--no-enrich", action="store_true", help="Do not normalize status fields into device_kv.")
    pi.add_argument("--verbose", action="store_true")
    pi.set_defaults(func=cmd_interrogate)

    # control
    control_examples = "\n".join(
        [
            "Examples:",
            "  govee-discovery control --ip 192.168.1.50 on",
            "  govee-discovery control --device-id ABCD1234 color red",
            "  govee-discovery control --ip 192.168.1.50 color #ff8800",
            "  govee-discovery control --ip 192.168.1.50 brightness 75",
            "  govee-discovery control --ip 192.168.1.50 color-temp 3500",
            "  govee-discovery control --ip 192.168.1.50 colorwc --kelvin 4000 --color #ffaa88",
            "  govee-discovery control --ip 192.168.1.50 colorwc --kelvin 2700",
            "  govee-discovery control --ip 192.168.1.50 color red --color-cmd colorwc --kelvin 3200",
            "  govee-discovery control --ip 192.168.1.50 color red --color-cmd setColor --color-scale 100",
        ]
    )
    pc = sub.add_parser(
        "control",
        help="Send LAN control commands to a device.",
        epilog=control_examples,
        formatter_class=argparse.RawTextHelpFormatter,
    )
    add_common_db_args(pc)
    pc.add_argument("--ip", help="Target device IP.")
    pc.add_argument("--device-id", help="Target device ID (lookup IP from registry).")
    pc.add_argument("--timeout", type=float, default=2.0, help="UDP receive timeout (seconds).")
    pc.add_argument("--no-wait", action="store_true", help="Do not wait for a response.")
    pc.add_argument("--pretty", action="store_true", help="Pretty-print any response JSON.")
    pc.add_argument("--verbose", action="store_true")

    pc_sub = pc.add_subparsers(dest="action", required=True)
    pc_sub.add_parser("on", help="Turn the device on.")
    pc_sub.add_parser("off", help="Turn the device off.")

    pc_color = pc_sub.add_parser("color", help="Set RGB color (name or hex) with optional Kelvin payload.")
    pc_color.add_argument("color", help="Color name (red) or hex (RRGGBB/#RRGGBB).")
    pc_color.add_argument(
        "--color-cmd",
        choices=["color", "colorwc", "setColor"],
        default="colorwc",
        help="Override the command used for RGB/WW/CW devices (colorwc is recommended for most devices).",
    )
    pc_color.add_argument(
        "--color-scale",
        choices=[100, 255],
        type=int,
        default=255,
        help="Scale RGB output to 0-100 for models that expect percentage values instead of 0-255.",
    )
    pc_color.add_argument(
        "--kelvin",
        type=int,
        default=None,
        help="Optional Kelvin to include when using --color-cmd colorwc for combined RGB/Kelvin payloads.",
    )

    pc_brightness = pc_sub.add_parser("brightness", help="Set brightness (0-100).")
    pc_brightness.add_argument("value", type=int, help="Brightness percent (0-100).")

    pc_ct = pc_sub.add_parser("color-temp", help="Set color temperature in Kelvin.")
    pc_ct.add_argument("kelvin", type=int, help="Color temperature in Kelvin (device range).")

    pc_colorwc = pc_sub.add_parser(
        "colorwc",
        help="Set Kelvin with optional RGB (color + warm/cool white) on dual-capability devices.",
    )
    pc_colorwc.add_argument("--kelvin", type=int, required=False, default=None, help="Color temperature in Kelvin.")
    pc_colorwc.add_argument(
        "--color",
        default=None,
        help="Optional color name (red) or hex (RRGGBB/#RRGGBB); defaults to warm/cool white when omitted.",
    )
    pc_colorwc.add_argument(
        "--color-cmd",
        choices=["color", "colorwc", "setColor"],
        default="colorwc",
        help="Override the command name used for combined color + Kelvin payloads.",
    )
    pc_colorwc.add_argument(
        "--color-scale",
        choices=[100, 255],
        type=int,
        default=255,
        help="Scale RGB output to 0-100 (some models expect 0-100 instead of 0-255).",
    )

    pc_probe_examples = "\n".join(
        [
            "Example:",
            "  govee-discovery control --ip 192.168.1.50 color-probe --verbose --stop-on-success",
        ]
    )
    pc_probe = pc_sub.add_parser(
        "color-probe",
        help="Try color variants (logs each attempt, pauses between tries, does not fail on missing replies).",
        description=(
            "Iterate through common color payload variants (color/colorwc/setColor/setColorWC) across RGB scales "
            "and Kelvin inclusion. Each attempt is logged, a 1s pause is inserted between attempts to avoid "
            "flooding, and missing replies are recorded as timeouts instead of failing the probe."
        ),
        epilog=pc_probe_examples,
        formatter_class=argparse.RawTextHelpFormatter,
    )
    pc_probe.add_argument(
        "--color",
        action="append",
        default=None,
        help="Probe with this color name or hex (repeatable). Default: red, green, blue.",
    )
    pc_probe.add_argument(
        "--kelvin",
        type=int,
        action="append",
        default=None,
        help="Probe with this Kelvin (repeatable). Default: 3000, 4000, 6500.",
    )
    pc_probe.add_argument(
        "--require-kelvin",
        action="store_true",
        help="Do not send payloads without colorTemInKelvin (skip pure RGB variants).",
    )
    pc_probe.add_argument(
        "--stop-on-success",
        action="store_true",
        help="Stop after the first response is received (useful for long probe lists).",
    )

    pc.set_defaults(func=cmd_control)

    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    rc = args.func(args)
    raise SystemExit(rc)
