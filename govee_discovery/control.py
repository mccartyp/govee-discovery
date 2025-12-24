from __future__ import annotations

import json
import re
import socket
from typing import Any, Optional

from .net import CONTROL_PORT, make_control_socket

COLOR_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")
COMMON_COLORS: dict[str, tuple[int, int, int]] = {
    "red": (255, 0, 0),
    "green": (0, 255, 0),
    "blue": (0, 0, 255),
    "white": (255, 255, 255),
    "warmwhite": (255, 244, 229),
    "yellow": (255, 255, 0),
    "orange": (255, 165, 0),
    "purple": (128, 0, 128),
    "pink": (255, 105, 180),
    "cyan": (0, 255, 255),
    "magenta": (255, 0, 255),
}


def safe_json_loads(data: bytes) -> Optional[dict[str, Any]]:
    try:
        return json.loads(data.decode("utf-8", errors="strict"))
    except Exception:
        return None


def parse_color(value: str) -> tuple[int, int, int]:
    normalized = value.strip().lower()
    if normalized in COMMON_COLORS:
        return COMMON_COLORS[normalized]

    match = COLOR_HEX_RE.match(normalized)
    if not match:
        raise ValueError(f"unsupported color value: {value!r}")

    hex_value = match.group(1)
    r = int(hex_value[0:2], 16)
    g = int(hex_value[2:4], 16)
    b = int(hex_value[4:6], 16)
    return r, g, b


def build_turn_command(on: bool) -> dict[str, Any]:
    return {"msg": {"cmd": "turn", "data": {"value": 1 if on else 0}}}


def build_brightness_command(value: int) -> dict[str, Any]:
    return {"msg": {"cmd": "brightness", "data": {"value": value}}}


def build_color_command(r: int, g: int, b: int) -> dict[str, Any]:
    return {"msg": {"cmd": "color", "data": {"color": {"r": r, "g": g, "b": b}}}}


def build_color_temp_command(kelvin: int) -> dict[str, Any]:
    return {"msg": {"cmd": "colorTem", "data": {"value": kelvin}}}


def build_colorwc_command(kelvin: int, color: tuple[int, int, int] | None) -> dict[str, Any]:
    data: dict[str, Any] = {"colorTemInKelvin": kelvin}
    if color is not None:
        r, g, b = color
        data["color"] = {"r": r, "g": g, "b": b}
    return {"msg": {"cmd": "colorwc", "data": data}}


def send_control_command(
    *,
    ip: str,
    payload: dict[str, Any],
    bind_ip: str,
    timeout_s: float,
    wait_response: bool,
) -> tuple[bool, Optional[dict[str, Any]], Optional[str]]:
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    sock = make_control_socket(bind_ip=bind_ip, timeout_s=timeout_s)
    try:
        sock.sendto(blob, (ip, CONTROL_PORT))
        if not wait_response:
            return True, None, None
        try:
            data, _addr = sock.recvfrom(8192)
        except socket.timeout:
            return True, None, "timeout"
        resp = safe_json_loads(data)
        if not isinstance(resp, dict):
            return True, None, "invalid_json"
        return True, resp, None
    except OSError as e:
        return False, None, f"oserror:{e}"
    except Exception as e:
        return False, None, f"error:{e}"
    finally:
        sock.close()
