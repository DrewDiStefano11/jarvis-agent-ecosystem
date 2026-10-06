"""Pure destination policy; validation is not execution authorization.

A future transport must connect only to these validated addresses, retain the
original hostname for TLS/Host, and revalidate every redirect and new connection.
Checking DNS and then letting an HTTP library resolve again is unsafe.
"""

import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, urlunsplit

from app.core.errors import DomainError

MAX_URL_BYTES = 4096
MAX_DNS_ANSWERS = 32
# Conservative exclusions stable across supported Python versions. IPv6
# transition mechanisms can embed another destination and are deliberately denied.
DENIED_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in (
        "192.0.0.0/24",
        "192.88.99.0/24",
        "168.63.129.16/32",
        "64:ff9b::/96",
        "64:ff9b:1::/48",
        "2001::/23",
        "2002::/16",
        "3fff::/20",
        "::ffff:0:0/96",
        "fec0::/10",
    )
)


def reject(code: str = "RESEARCH_URL_INVALID") -> None:
    # Never echo URLs, DNS answers, credentials or resolver exception text.
    raise DomainError(code, "Research destination rejected by policy.", 422) from None


def public_address(value: str) -> str:
    if not isinstance(value, str) or len(value) > 45 or "%" in value:
        reject("RESEARCH_ADDRESS_DENIED")
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        reject("RESEARCH_ADDRESS_DENIED")
    if (
        not address.is_global
        or address.is_multicast
        or address.is_reserved
        or any(address in network for network in DENIED_NETWORKS)
    ):
        reject("RESEARCH_ADDRESS_DENIED")
    return str(address)


@dataclass(frozen=True)
class ResearchDestination:
    url: str
    hostname: str
    port: int


def normalize_url(value: str) -> ResearchDestination:
    if not isinstance(value, str) or not value or len(value) > MAX_URL_BYTES:
        reject()
    # urlsplit strips some controls, so reject them before parsing. Require ASCII
    # URL paths; callers can percent-encode Unicode paths explicitly.
    if any(ord(char) <= 32 or ord(char) >= 127 for char in value) or "\\" in value:
        reject()
    if re.search(r"%(?![0-9a-fA-F]{2})|%(?:0[0-9a-fA-F]|1[0-9a-fA-F]|7[fF])", value):
        reject()
    try:
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            reject()
        if parts.username is not None or parts.password is not None or "#" in value:
            reject()
        if not re.fullmatch(r"(?:\[[0-9a-fA-F:]+\]|[a-zA-Z0-9.-]+)(?::(?:80|443))?", parts.netloc):
            reject()
        hostname = parts.hostname
        port = parts.port if parts.port is not None else (443 if parts.scheme == "https" else 80)
        if (
            hostname is None
            or parts.netloc.endswith(":")
            or port != (443 if parts.scheme == "https" else 80)
        ):
            reject()
        if "%" in hostname:
            reject()
        hostname = hostname.lower().removesuffix(".")
        try:
            ipaddress.ip_address(hostname)
        except ValueError:
            labels = hostname.split(".")
            if (
                len(hostname) > 253
                or len(labels) < 2
                or any(
                    not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                    for label in labels
                )
                or not re.search(r"[a-z]", labels[-1])
                or labels[-1]
                in {"localhost", "local", "internal", "invalid", "test", "example", "onion"}
                or any(label.startswith("0x") for label in labels)
            ):
                reject()
            authority = hostname
        else:
            hostname = public_address(hostname)
            authority = f"[{hostname}]" if ":" in hostname else hostname
        normalized = urlunsplit((parts.scheme, authority, parts.path or "/", parts.query, ""))
        return ResearchDestination(normalized, hostname, port)
    except (ValueError, UnicodeError):
        reject()


def validate_dns_answers(
    destination: ResearchDestination, answers: tuple[str, ...]
) -> tuple[str, ...]:
    """Reject the whole answer set if any result is unsafe, malformed or absent."""
    if not answers or len(answers) > MAX_DNS_ANSWERS:
        reject("RESEARCH_ADDRESS_DENIED")
    validated = tuple(dict.fromkeys(public_address(answer) for answer in answers))
    try:
        literal = ipaddress.ip_address(destination.hostname)
    except ValueError:
        pass
    else:
        if validated != (str(literal),):
            reject("RESEARCH_ADDRESS_DENIED")
    return validated


def redirect_destination(
    current: ResearchDestination, location: str, *, hops: int, maximum_hops: int = 3
) -> ResearchDestination:
    if not 0 <= maximum_hops <= 5 or not 0 <= hops < maximum_hops:
        reject("RESEARCH_REDIRECT_LIMIT")
    # Validate raw Location before urljoin can strip controls or replace a scheme.
    if (
        not location
        or len(location) > MAX_URL_BYTES
        or any(ord(char) <= 32 or ord(char) >= 127 for char in location)
        or "\\" in location
    ):
        reject()
    target = normalize_url(urljoin(current.url, location))
    if current.url.startswith("https:") and target.url.startswith("http:"):
        reject("RESEARCH_REDIRECT_DENIED")
    return target
