"""Outbound destination restriction for NVR connections (spec: restricted outbound destination access).

Private LAN and tunnel addresses are the normal case, so they are allowed. Addresses that would let an admin-supplied
host reach the server itself or cloud metadata services are refused.
"""

import ipaddress
import socket

from app.integrations.base import NvrError


class HostNotAllowedError(NvrError):
    pass


def _check_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
    if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
        raise HostNotAllowedError("This address is not allowed as an NVR host (loopback, link-local or reserved)")


def assert_host_allowed(host: str) -> None:
    try:
        _check_ip(ipaddress.ip_address(host))
        return
    except ValueError:
        pass  # a hostname, resolve it
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        from app.integrations.base import NvrUnreachableError

        raise NvrUnreachableError(f"Cannot resolve host name '{host}'") from exc
    for info in infos:
        _check_ip(ipaddress.ip_address(info[4][0]))
