"""Validate every network target before transport.

This is not a complete DNS-rebinding defense: HTTPX and Playwright resolve the
hostname again after validation, and the checked address is not pinned to the
connection. Deploy public crawlers with network-level egress restrictions that
block private, loopback, link-local and metadata networks as defense in depth.
"""
import ipaddress
import socket
from urllib.parse import urlsplit


def _resolve(host, port):
    return [answer[4][0] for answer in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)]


def _global_address(value: str) -> bool:
    address = ipaddress.ip_address(value)
    if not address.is_global or getattr(address, 'is_site_local', False):
        return False
    if isinstance(address, ipaddress.IPv6Address):
        # Transition prefixes can route to an embedded IPv4 address even when
        # the IPv6 prefix itself is reported global on older Python releases.
        if address in ipaddress.ip_network('64:ff9b:1::/48'):
            return False
        if address in ipaddress.ip_network('64:ff9b::/96'):
            return ipaddress.IPv4Address(int(address) & 0xffffffff).is_global
        embedded = address.ipv4_mapped or address.sixtofour
        if embedded and not embedded.is_global:
            return False
        if address.teredo and not all(part.is_global for part in address.teredo):
            return False
    return True


def validate_public_url(url, resolver=None) -> str:
    try:
        parts = urlsplit(url)
        if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username is not None or parts.password is not None:
            raise ValueError('invalid URL')
        if parts.port not in (None, 80 if parts.scheme == 'http' else 443):
            raise ValueError('nonstandard port')
        host = parts.hostname.rstrip('.')
        if host.lower() == 'localhost' or host.lower().endswith('.localhost'):
            raise ValueError('localhost')
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError('IP literal')
        if any(ch.isspace() or ord(ch) < 32 for ch in url):
            raise ValueError('invalid URL')
        answers = (resolver or _resolve)(host, parts.port or (443 if parts.scheme == 'https' else 80))
        if not answers or any(not _global_address(a) for a in answers):
            raise ValueError('non-public address')
    except (ValueError, OSError, KeyError) as exc:
        raise ValueError(f'unsafe URL: {url}') from exc
    return url
