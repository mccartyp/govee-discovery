from __future__ import annotations

import json
import socket
import time
from typing import Any, Optional

from .net import CONTROL_PORT, make_control_socket
from .store import RegistryStore, now_ms


def safe_json_loads(data: bytes) -> Optional[dict[str, Any]]:
    try:
        return json.loads(data.decode("utf-8", errors="strict"))
    except Exception:
        return None


def build_dev_status_request(device_id: Optional[str] = None, sku: Optional[str] = None) -> dict[str, Any]:
    data: dict[str, Any] = {}
    if device_id:
        data["device"] = device_id
    if sku:
        data["sku"] = sku
    return {"msg": {"cmd": "devStatus", "data": data}}


def is_dev_status_response(obj: dict[str, Any]) -> bool:
    msg = obj.get("msg")
    if not isinstance(msg, dict):
        return False
    return msg.get("cmd") == "devStatus"


def extract_status_data(obj: dict[str, Any]) -> Optional[dict[str, Any]]:
    msg = obj.get("msg")
    if not isinstance(msg, dict):
        return None
    data = msg.get("data")
    if not isinstance(data, dict):
        return None
    return data


def interrogate_device_dev_status(
    sock: socket.socket, ip: str, device_id: Optional[str] = None, sku: Optional[str] = None
) -> tuple[bool, Optional[dict[str, Any]], Optional[str]]:
    req = build_dev_status_request(device_id=device_id, sku=sku)
    blob = json.dumps(req, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    orig_timeout = sock.gettimeout()
    deadline = (time.monotonic() + orig_timeout) if orig_timeout is not None else None

    try:
        sock.sendto(blob, (ip, CONTROL_PORT))
        while True:
            try:
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise socket.timeout
                    sock.settimeout(remaining)
                data, _addr = sock.recvfrom(8192)
            except socket.timeout:
                return False, None, "timeout"
            except OSError as e:
                return False, None, f"oserror:{e}"
            except Exception as e:
                return False, None, f"error:{e}"

            obj = safe_json_loads(data)
            if isinstance(obj, dict):
                break
    finally:
        if orig_timeout is not None:
            sock.settimeout(orig_timeout)

    if not is_dev_status_response(obj):
        # Preserve unexpected responses; still store them.
        return True, obj, "unexpected_cmd"
    return True, obj, None


def enrich_from_dev_status(store: RegistryStore, device_id: str, status_obj: dict[str, Any]) -> None:
    data = extract_status_data(status_obj)
    if not data:
        return

    if "onOff" in data:
        store.set_kv(device_id, "status.onOff", data["onOff"])
    if "brightness" in data:
        store.set_kv(device_id, "status.brightness", data["brightness"])
    if "color" in data:
        store.set_kv(device_id, "status.color", data["color"])
    if "colorTemInKelvin" in data:
        store.set_kv(device_id, "status.colorTemInKelvin", data["colorTemInKelvin"])


def interrogate_all(
    store: RegistryStore,
    bind_ip: str,
    timeout_s: float,
    verbose: bool,
    enrich: bool,
    only_ips: Optional[list[str]] = None,
    target_ips: Optional[list[str]] = None,
) -> None:
    sock = make_control_socket(bind_ip=bind_ip, listen_port=CONTROL_PORT, timeout_s=timeout_s)

    # Build target list, either from explicit IPs or discovered devices.
    if target_ips is not None:
        known_targets = {t["ip"]: t for t in store.list_device_targets()}
        seen_ips: set[str] = set()
        targets = []
        for ip in target_ips:
            if ip in seen_ips:
                continue
            seen_ips.add(ip)
            known = known_targets.get(ip)
            targets.append(
                {
                    "device_id": known["device_id"] if known else None,
                    "ip": ip,
                    "sku": known["sku"] if known else None,
                }
            )
    else:
        targets = store.list_device_targets()
        if only_ips:
            allowed = set(only_ips)
            targets = [t for t in targets if t["ip"] in allowed]

    for t in targets:
        device_id = t["device_id"]
        ip = t["ip"]
        sku = t.get("sku")
        sent = now_ms()

        ok, resp, err = interrogate_device_dev_status(sock, ip=ip, device_id=device_id, sku=sku)

        received = now_ms() if ok else None
        store.record_interrogation(
            device_id=device_id,
            ip=ip,
            cmd="devStatus",
            sent_at_ms=sent,
            received_at_ms=received,
            success=ok,
            error=err,
            request_obj=build_dev_status_request(device_id=device_id, sku=sku),
            response_obj=resp,
        )

        if ok and resp and enrich and device_id:
            enrich_from_dev_status(store, device_id=device_id, status_obj=resp)

        if verbose:
            print(f"[devStatus] ip={ip} device={device_id} ok={ok} err={err or '-'}", flush=True)

    sock.close()
