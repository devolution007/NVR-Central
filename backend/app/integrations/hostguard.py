"""Outbound destination restriction for NVR connections (spec: restricted outbound destination access).

Private LAN and tunnel addresses are the normal case, so they are allowed. Addresses that would let an admin-supplied
host reach the server itself or cloud metadata services are refused.
"""

import ipaddress
import socket

from app.core.config import get_settings
from app.integrations.base import NvrError, NvrUnreachableError


class HostNotAllowedError(NvrError):
    pass


def _check_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
    if ip.is_loopback and get_settings().allow_loopback_nvr_hosts:
        return
    if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
        raise HostNotAllowedError("This address is not allowed as an NVR host (loopback, link-local or reserved)")


def assert_host_allowed(host: str) -> None:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        pass  # a hostname: resolve it and check every address it maps to
    else:
        _check_ip(ip)
        return
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise NvrUnreachableError(f"Cannot resolve host name '{host}'") from exc
    for info in infos:
        _check_ip(ipaddress.ip_address(info[4][0]))
