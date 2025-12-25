from __future__ import annotations

import socket

MCAST_GRP = "239.255.255.250"
SCAN_PORT = 4001
LISTEN_PORT = 4002
CONTROL_PORT = 4003


def make_bound_socket(bind_ip: str = "", listen_port: int = 0, timeout_s: float = 2.0) -> socket.socket:
    """UDP socket bound to a specific local address/port.

    Used for control/status responses where multicast membership is not required.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    bind_addr = bind_ip if bind_ip else "0.0.0.0"
    s.bind((bind_addr, listen_port))
    s.settimeout(timeout_s)
    return s


def make_mcast_sender_socket(bind_ip: str = "") -> socket.socket:
    """
    UDP socket for sending multicast scan request to 239.255.255.250:4001.

    TTL=1 is typical for discovery. bind_ip forces the egress interface on multi-homed hosts.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    if bind_ip:
        s.bind((bind_ip, 0))
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(bind_ip))

    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
    return s


def make_listener_socket(bind_ip: str = "") -> socket.socket:
    """
    UDP socket bound to port 4002 for scan responses and joined to multicast group.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    bind_addr = bind_ip if bind_ip else "0.0.0.0"
    s.bind((bind_addr, LISTEN_PORT))

    # Join multicast group (defensive; some environments relay/reflect multicast)
    mreq = socket.inet_aton(MCAST_GRP) + socket.inet_aton(bind_ip if bind_ip else "0.0.0.0")
    s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    return s


def make_control_socket(bind_ip: str = "", listen_port: int = 0, timeout_s: float = 2.0) -> socket.socket:
    """
    UDP socket for unicast control/status queries to device port 4003.

    listen_port allows binding to a specific local port (e.g., 4003 for replies).
    Default is 0 for an ephemeral port when no response is expected.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if bind_ip or listen_port:
        bind_addr = bind_ip if bind_ip else "0.0.0.0"
        s.bind((bind_addr, listen_port))
    s.settimeout(timeout_s)
    return s
