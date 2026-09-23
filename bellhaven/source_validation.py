from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


def validate_public_https_url(value: str) -> str:
    """Return a normalized public HTTPS base URL or raise a user-safe error."""
    value = (value or "").strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Enter a public HTTPS URL without embedded credentials.")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("Enter the operator site's base URL, without a path, query, or fragment.")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)}
    except socket.gaierror as exc:
        raise ValueError("The source hostname could not be resolved.") from exc
    if not addresses:
        raise ValueError("The source hostname did not resolve to an address.")
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise ValueError("Private, loopback, link-local, and reserved source addresses are not allowed.")
    return value
