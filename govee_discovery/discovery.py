from __future__ import annotations

import asyncio
import json
import subprocess
from typing import Any, Optional, Tuple

from .net import MCAST_GRP, SCAN_PORT, make_listener_socket, make_mcast_sender_socket
from .store import RegistryStore, now_ms


def safe_json_loads(data: bytes) -> Optional[dict[str, Any]]:
    try:
        return json.loads(data.decode("utf-8", errors="strict"))
    except Exception:
        return None


def parse_scan_response(obj: dict[str, Any]) -> Optional[dict[str, Any]]:
    msg = obj.get("msg")
    if not isinstance(msg, dict):
        return None
    if msg.get("cmd") != "scan":
        return None
    data = msg.get("data")
    if not isinstance(data, dict):
        return None
    return data


def ip_neigh_mac(ip: str) -> Optional[str]:
    """
    Best-effort: resolves MAC from neighbor table if present.
    Across routed VLANs this is often unavailable.
    """
    try:
        out = subprocess.check_output(["ip", "neigh", "show", ip], text=True, stderr=subprocess.DEVNULL).strip()
        if not out:
            return None
        parts = out.split()
        if "lladdr" in parts:
            idx = parts.index("lladdr")
            if idx + 1 < len(parts):
                return parts[idx + 1].lower()
        return None
    except Exception:
        return None


class DiscoveryProtocol(asyncio.DatagramProtocol):
    def __init__(self, store: RegistryStore, resolve_mac: bool, verbose: bool) -> None:
        self.store = store
        self.resolve_mac = resolve_mac
        self.verbose = verbose

    def datagram_received(self, data: bytes, addr: Tuple[str, int]) -> None:
        src_ip, src_port = addr
        received_at = now_ms()
        payload_str = data.decode("utf-8", errors="replace")

        # Always persist raw event
        self.store.insert_scan_event(received_at, src_ip, src_port, payload_str)

        parsed = safe_json_loads(data)
        if not isinstance(parsed, dict):
            if self.verbose:
                print(f"[scan] {src_ip}:{src_port} unparsed: {payload_str[:160]!r}", flush=True)
            return

        scan_data = parse_scan_response(parsed)
        if not scan_data:
            if self.verbose:
                cmd = parsed.get("msg", {}).get("cmd") if isinstance(parsed.get("msg"), dict) else None
                print(f"[scan] {src_ip}:{src_port} ignored cmd={cmd!r}", flush=True)
            return

        ip = scan_data.get("ip")
        device_id = scan_data.get("device")
        sku = scan_data.get("sku")
        ble_hw = scan_data.get("bleVersionHard")
        ble_sw = scan_data.get("bleVersionSoft")
        wifi_hw = scan_data.get("wifiVersionHard")
        wifi_sw = scan_data.get("wifiVersionSoft")

        mac = ip_neigh_mac(ip) if (self.resolve_mac and isinstance(ip, str)) else None

        if isinstance(device_id, str) and device_id:
            self.store.upsert_device_from_scan(
                device_id=device_id,
                ip=ip if isinstance(ip, str) else None,
                sku=sku if isinstance(sku, str) else None,
                ble_hw=ble_hw if isinstance(ble_hw, str) else None,
                ble_sw=ble_sw if isinstance(ble_sw, str) else None,
                wifi_hw=wifi_hw if isinstance(wifi_hw, str) else None,
                wifi_sw=wifi_sw if isinstance(wifi_sw, str) else None,
                mac=mac,
                scan_payload_raw=json.dumps(parsed, ensure_ascii=False, separators=(",", ":")),
                seen_ms=received_at,
            )

        if self.verbose:
            print(
                f"[scan] ip={ip} device={device_id} sku={sku} mac={mac or '-'}",
                flush=True,
            )


async def send_scan_requests(bind_ip: str, repeat: int, interval_s: float) -> None:
    payload = {"msg": {"cmd": "scan", "data": {"account_topic": "reserve"}}}
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    sock = make_mcast_sender_socket(bind_ip=bind_ip)
    try:
        for i in range(max(1, repeat)):
            sock.sendto(blob, (MCAST_GRP, SCAN_PORT))
            if i + 1 < repeat:
                await asyncio.sleep(interval_s)
    finally:
        sock.close()


async def run_listener(
    store: RegistryStore,
    bind_ip: str,
    resolve_mac: bool,
    verbose: bool,
) -> None:
    loop = asyncio.get_running_loop()
    sock = make_listener_socket(bind_ip=bind_ip)
    await loop.create_datagram_endpoint(
        lambda: DiscoveryProtocol(store=store, resolve_mac=resolve_mac, verbose=verbose),
        sock=sock,
    )


async def run_scan(
    store: RegistryStore,
    bind_ip: str,
    resolve_mac: bool,
    verbose: bool,
    scan_repeat: int,
    scan_interval: float,
) -> None:
    await run_listener(store=store, bind_ip=bind_ip, resolve_mac=resolve_mac, verbose=verbose)
    await send_scan_requests(bind_ip=bind_ip, repeat=scan_repeat, interval_s=scan_interval)
