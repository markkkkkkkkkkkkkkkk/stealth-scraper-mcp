"""SSRF guard: refuse URLs that resolve to non-public addresses."""

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

from . import config


class BlockedURLError(ValueError):
    pass


def _is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


async def resolve_public(host: str, port: int) -> str:
    """Resolve host and return an address to connect to, raising BlockedURLError if any
    resolved address is non-public. Callers must connect to the returned address rather
    than resolving again, or a DNS answer that changes in between (rebinding) slips past.
    """
    try:
        addresses = [ipaddress.ip_address(host.strip("[]"))]
    except ValueError:
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise BlockedURLError(f"Could not resolve host '{host}': {exc}") from exc
        addresses = [ipaddress.ip_address(info[4][0].split("%")[0]) for info in infos]
    if not addresses:
        raise BlockedURLError(f"Could not resolve host '{host}'.")
    if not config.ALLOW_PRIVATE_NETWORKS:
        for ip in addresses:
            if not _is_public(ip):
                raise BlockedURLError(f"Host '{host}' resolves to non-public address {ip}.")
    return str(addresses[0])


async def ensure_public_url(url: str) -> str:
    """Raise BlockedURLError unless url is http(s) and its host resolves only to public
    addresses; return the address to connect to.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise BlockedURLError(f"Only http and https URLs are supported, got '{parts.scheme or 'none'}'.")
    if not parts.hostname:
        raise BlockedURLError("URL has no host.")
    return await resolve_public(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))
