from __future__ import annotations

import json
import re
import socket
import time
from dataclasses import dataclass
from typing import Any, Iterable, Optional, Sequence

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
    return {"msg": {"cmd": "colorwc", "data": {"color": {"r": r, "g": g, "b": b}}}}


def build_color_temp_command(kelvin: int) -> dict[str, Any]:
    return {"msg": {"cmd": "colorwc", "data": {"colorTemInKelvin": kelvin}}}


def build_colorwc_command(kelvin: int, color: tuple[int, int, int] | None) -> dict[str, Any]:
    data: dict[str, Any] = {"colorTemInKelvin": kelvin}
    if color is not None:
        r, g, b = color
        data["color"] = {"r": r, "g": g, "b": b}
    return {"msg": {"cmd": "colorwc", "data": data}}


def _scale_color(color: tuple[int, int, int], scale_max: int) -> tuple[int, int, int]:
    if scale_max not in (100, 255):
        raise ValueError("color scale must be 100 or 255")
    if scale_max == 255:
        return color
    r, g, b = color
    scaled = (
        round((r / 255) * scale_max),
        round((g / 255) * scale_max),
        round((b / 255) * scale_max),
    )
    return scaled


def build_color_payload(
    command: str,
    *,
    color: tuple[int, int, int] | None,
    kelvin: int | None,
    scale_max: int = 255,
) -> dict[str, Any]:
    cmd = command.strip()
    if cmd not in {"color", "colorwc", "setColor", "setColorWC"}:
        raise ValueError(f"unsupported color command: {cmd}")
    if scale_max not in (100, 255):
        raise ValueError("color scale must be 100 or 255")

    data: dict[str, Any] = {}
    if color is not None:
        r, g, b = _scale_color(color, scale_max)
        data["color"] = {"r": r, "g": g, "b": b}

    if kelvin is not None:
        if kelvin <= 0:
            raise ValueError("kelvin must be positive when provided")
        data["colorTemInKelvin"] = kelvin

    if cmd in {"color", "setColor"} and "color" not in data:
        raise ValueError(f"{cmd} command requires a color value")
    if cmd in {"colorwc", "setColorWC"} and "colorTemInKelvin" not in data:
        raise ValueError(f"{cmd} command requires a positive kelvin value")

    return {"msg": {"cmd": cmd, "data": data}}


@dataclass
class ColorProbeResult:
    command: str
    scale_max: int
    kelvin: int | None
    color_name: str
    payload: dict[str, Any]
    ok: bool
    resp: Optional[dict[str, Any]]
    error: Optional[str]

    def status(self) -> str:
        if not self.ok:
            return self.error or "error"
        if self.resp is not None:
            msg = self.resp.get("msg") if isinstance(self.resp, dict) else None
            if isinstance(msg, dict):
                code = msg.get("code")
                if code is not None:
                    return f"resp code={code}"
            return "resp"
        if self.error:
            return self.error
        return "no_resp"


def iter_color_probe_payloads(
    *,
    colors: Sequence[tuple[str, tuple[int, int, int]]],
    kelvin_values: Sequence[int],
    include_no_kelvin: bool = True,
    scale_values: Iterable[int] = (255, 100),
) -> Iterable[tuple[str, int, int | None, str, dict[str, Any]]]:
    commands = ("color", "colorwc", "setColor", "setColorWC")
    for color_name, color_value in colors:
        for cmd in commands:
            for scale in scale_values:
                kelvin_candidates: list[int | None] = list(kelvin_values)
                if include_no_kelvin and cmd in {"color", "setColor"}:
                    kelvin_candidates = [None, *kelvin_candidates]
                for kelvin in kelvin_candidates:
                    if cmd in {"colorwc", "setColorWC"} and kelvin is None:
                        continue
                    try:
                        payload = build_color_payload(cmd, color=color_value, kelvin=kelvin, scale_max=scale)
                    except ValueError:
                        continue
                    yield cmd, scale, kelvin, color_name, payload


def run_color_probe(
    *,
    ip: str,
    colors: Sequence[tuple[str, tuple[int, int, int]]],
    kelvin_values: Sequence[int],
    include_no_kelvin: bool,
    bind_ip: str,
    timeout_s: float,
    stop_on_success: bool,
    verbose: bool,
) -> list[ColorProbeResult]:
    results: list[ColorProbeResult] = []
    first = True
    for cmd, scale, kelvin, color_name, payload in iter_color_probe_payloads(
        colors=colors,
        kelvin_values=kelvin_values,
        include_no_kelvin=include_no_kelvin,
    ):
        if not first:
            time.sleep(1.0)
        first = False
        if verbose:
            payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            print(
                f"[probe] ip={ip} cmd={cmd} scale={scale} kelvin={kelvin or '-'} color={color_name} payload={payload_json}",
                flush=True,
            )
        ok, resp, err = send_control_command(
            ip=ip,
            payload=payload,
            bind_ip=bind_ip,
            timeout_s=timeout_s,
            wait_response=True,
        )
        result = ColorProbeResult(
            command=cmd,
            scale_max=scale,
            kelvin=kelvin,
            color_name=color_name,
            payload=payload,
            ok=ok,
            resp=resp,
            error=err,
        )
        results.append(result)
        status = result.status()
        print(
            f"[probe] cmd={cmd} scale={scale} kelvin={kelvin if kelvin is not None else '-'} color={color_name} status={status}",
            flush=True,
        )
        if stop_on_success and result.ok and result.resp is not None:
            break
    return results


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
