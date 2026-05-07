from __future__ import annotations

import ipaddress
import os
import re
from dataclasses import dataclass
from urllib.parse import quote, urljoin, urlsplit, urlunsplit


class SafetyError(ValueError):
    """Marker base for any error that must propagate as `unsafe_url_blocked`
    (exit code 4) rather than be reclassified by a provider as a generic
    operational failure.

    Provider error handling MUST re-raise this class:

        except SafetyError:
            raise
        except Exception as exc:
            return _result(status="provider_unavailable", ...)

    Without the explicit re-raise, a `except Exception` block silently masks
    SSRF / host-header-smuggling / redirect-validation failures as
    `provider_unavailable` (exit 1), losing the security signal and the
    correct exit code.
    """


class UnsafeUrl(SafetyError):
    pass


@dataclass(frozen=True)
class ResolvedTarget:
    """One resolved DNS answer for a hostname.

    `family` is `"AF_INET"` or `"AF_INET6"` so the connector can pick the
    right socket family without re-resolving (and re-introducing TOCTOU)."""

    hostname: str
    ip: str
    family: str


def blocked_resolved_targets(
    targets: list[ResolvedTarget], *, allow_loopback: bool = False
) -> str | None:
    """Return a non-None error code if ANY resolved target is blocked.

    Refusing the entire hostname when even one answer is private prevents
    DNS rebinding and mixed-public/private CNAME attacks. If the resolver
    returned no answers at all, treat that as a setup failure rather than
    silently passing.

    `allow_loopback=True` exempts 127.0.0.0/8 and ::1 from the block — used
    by SafeHttpClient(loopback_ok=True) for the CDP transport, whose
    intended path is the user's own browser on 127.0.0.1. All other
    blocked categories (link-local, private RFC 1918, IMDS literals, etc.)
    remain rejected even in loopback mode.
    """
    if not targets:
        return "dns_resolution_empty"
    for target in targets:
        ip_obj = _parse_ip(target.ip)
        if ip_obj is None:
            return "blocked_resolved_ip"
        if is_blocked_ip(ip_obj, allow_loopback=allow_loopback):
            return "blocked_resolved_ip"
    return None


def _env_allows_https_downgrade() -> bool:
    return os.environ.get("BFR_ALLOW_HTTPS_DOWNGRADE", "").lower() in {"1", "true", "yes"}


def validate_redirect(
    source_url: str,
    location: str,
    *,
    allow_https_downgrade: bool | None = None,
    allow_loopback: bool = False,
) -> str:
    """Re-validate a redirect target against the same SSRF policy as the
    initial URL, plus an HTTPS-downgrade gate.

    `allow_loopback` MUST mirror the caller's transport-level loopback
    policy. Previously this argument was missing — the redirect target
    was always validated with `allow_loopback=False` regardless of the
    initial URL's mode. That created a hidden coupling: any caller
    that set `SafeHttpClient(loopback_ok=True)` had to ALSO set
    `follow_redirects=False`, otherwise a redirect from the
    loopback-permitted initial URL would be rejected as `blocked_ip`
    even though the transport intended to permit loopback. Today the
    only `loopback_ok=True` caller (CDP) does set
    `follow_redirects=False`, so the gap was unreachable. Closing
    structurally lets a future caller pair the two flags as needed
    without re-introducing the hidden invariant.
    """
    if allow_https_downgrade is None:
        allow_https_downgrade = _env_allows_https_downgrade()
    target = normalize_and_validate_url(
        urljoin(source_url, location), allow_loopback=allow_loopback
    )
    source = urlsplit(source_url)
    parsed = urlsplit(target)
    if (
        source.scheme == "https"
        and parsed.scheme == "http"
        and not allow_https_downgrade
    ):
        raise UnsafeUrl("https_downgrade_redirect")
    return target


BLOCKED_HOSTS = {"localhost", "metadata.google.internal", "metadata", "metadata.azure.com"}

HOSTNAME_ALIASES = {
    "twitter.com": "x.com",
    "www.twitter.com": "x.com",
    "mobile.twitter.com": "x.com",
    "m.twitter.com": "x.com",
    "www.x.com": "x.com",
    "old.reddit.com": "www.reddit.com",
    "np.reddit.com": "www.reddit.com",
}

# Always-blocked IP literals beyond what stdlib flags (anycast, AWS metadata, etc).
EXTRA_BLOCKED_IPS = {
    "169.254.169.254",  # AWS/GCP IMDS
    "169.254.170.2",    # ECS task metadata
    "0.0.0.0",
}


def _parse_ip(host: str) -> ipaddress._BaseAddress | None:
    """Parse a hostname into an IP address if it looks like a numeric literal.

    Catches obfuscated forms (octal, hex, integer, shortened IPv4, IPv4-mapped
    IPv6) by mechanically rejecting any leading-zero or 0x-prefixed dotted part
    and by trying integer/hex/octal interpretations of all-numeric hostnames.
    """
    cleaned = host.strip("[]").split("%", 1)[0]
    if not cleaned:
        return None
    # Direct parse handles canonical forms (1.2.3.4 and ::1).
    try:
        return ipaddress.ip_address(cleaned)
    except ValueError:
        pass
    if "." in cleaned:
        dotted_parts = cleaned.split(".")
        # Reject any leading-zero or 0x-prefixed dotted octet — these are
        # commonly used to bypass naive SSRF filters.
        for part in dotted_parts:
            lowered = part.lower()
            if lowered.startswith("0x"):
                raise UnsafeUrl("blocked_ip")
            if len(part) > 1 and part.startswith("0") and part.isdigit():
                raise UnsafeUrl("blocked_ip")
        if any(part.isdigit() and int(part, 10) > 255 for part in dotted_parts):
            raise UnsafeUrl("blocked_ip")
    # Shortened IPv4: 127.1 → 127.0.0.1, 1.2.3 → 1.2.0.3.
    if re.fullmatch(r"[0-9]+(\.[0-9]+){1,2}", cleaned):
        parts = [int(part, 10) for part in cleaned.split(".")]
        if len(parts) == 2:
            parts = [parts[0], 0, 0, parts[1]]
        elif len(parts) == 3:
            parts = [parts[0], parts[1], 0, parts[2]]
        if any(p > 255 for p in parts):
            raise UnsafeUrl("blocked_ip")
        return ipaddress.ip_address(".".join(str(part) for part in parts))
    if re.fullmatch(r"0x[0-9a-fA-F]+", cleaned):
        try:
            return ipaddress.ip_address(int(cleaned, 16))
        except ValueError as exc:
            raise UnsafeUrl("blocked_ip") from exc
    if re.fullmatch(r"0[0-7]+", cleaned):
        try:
            return ipaddress.ip_address(int(cleaned, 8))
        except ValueError as exc:
            raise UnsafeUrl("blocked_ip") from exc
    if re.fullmatch(r"[0-9]+", cleaned):
        try:
            return ipaddress.ip_address(int(cleaned, 10))
        except ValueError as exc:
            raise UnsafeUrl("blocked_ip") from exc
    return None


def is_blocked_ip(
    ip: ipaddress._BaseAddress, *, allow_loopback: bool = False
) -> bool:
    """Return True for any IP that must never be reachable from this CLI.

    `allow_loopback=True` exempts 127.0.0.0/8 and ::1 — used by the CDP
    transport (SafeHttpClient(loopback_ok=True)) whose intended path is
    the user's own browser on 127.0.0.1. EXTRA_BLOCKED_IPS (IMDS, etc.)
    and all other categories (private RFC 1918, link-local, multicast,
    reserved, unspecified) remain blocked even in loopback mode.
    """
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    if str(ip) in EXTRA_BLOCKED_IPS:
        return True
    if allow_loopback and ip.is_loopback:
        return False
    return bool(
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


# Reject any C0 (0x00–0x1F) or C1 (0x7F–0x9F) control character anywhere in
# the URL, BEFORE handing it to urlsplit. Python 3.6+ silently strips \t\r\n
# from urlsplit output (a CPython security hardening), but the spec doesn't
# guarantee that and we don't want our security to depend on that behavior —
# nor do we want silent normalization of attacker input. Fail loud instead.
_URL_FORBIDDEN_CHARS_PATTERN = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def normalize_and_validate_url(url: str, *, allow_loopback: bool = False) -> str:
    """Canonicalize and SSRF-validate `url`.

    `allow_loopback=True` permits 127.0.0.0/8, ::1, and the literal
    hostname `localhost` — used by the CDP transport whose intended path
    is the user's own browser on 127.0.0.1. All other SSRF rejections
    (link-local, private RFC 1918, blocked metadata hosts, embedded
    credentials, blocked schemes/ports, control chars, single-label
    hosts) remain in force regardless of loopback mode.
    """
    if not isinstance(url, str) or not url.strip():
        raise UnsafeUrl("invalid_url")
    # Defense-in-depth against URL-component CRLF/NUL injection: a URL like
    # `http://example.com/foo\r\nX-Injected: evil` would, in any parser that
    # preserved control bytes, end up interpolated verbatim into the request
    # line and split it. We reject the raw input upfront so the transport
    # never sees those bytes regardless of the parser's behavior.
    if _URL_FORBIDDEN_CHARS_PATTERN.search(url):
        raise UnsafeUrl("control_chars_in_url")
    parsed = urlsplit(url.strip())
    if parsed.scheme not in {"http", "https"}:
        raise UnsafeUrl("blocked_scheme")
    if parsed.username or parsed.password:
        raise UnsafeUrl("embedded_credentials")
    raw_host = parsed.hostname or ""
    if not raw_host:
        raise UnsafeUrl("missing_host")
    try:
        port = parsed.port
    except ValueError as exc:
        raise UnsafeUrl("invalid_port") from exc
    if port is not None and not allow_loopback and (
        (parsed.scheme == "http" and port != 80)
        or (parsed.scheme == "https" and port != 443)
    ):
        # Public-internet path enforces default ports only — non-standard
        # ports are common SSRF amplifiers (mDNS, printers, internal admin
        # UIs). The loopback path (CDP) legitimately addresses the user's
        # browser on 9222 / 9223 / etc., so the port restriction lifts
        # under allow_loopback. The IP+host loopback rejection still
        # filters out non-loopback targets reachable via the same client.
        raise UnsafeUrl("blocked_port")
    ip_candidate = _parse_ip(raw_host)
    if ip_candidate is not None:
        if is_blocked_ip(ip_candidate, allow_loopback=allow_loopback):
            raise UnsafeUrl("blocked_ip")
        # Re-render canonical form so audit and cache keys stable.
        if isinstance(ip_candidate, ipaddress.IPv6Address):
            host = f"[{ip_candidate.compressed}]"
        else:
            host = str(ip_candidate)
    else:
        try:
            host = raw_host.encode("idna").decode("ascii").lower()
        except (UnicodeError, UnicodeDecodeError) as exc:
            raise UnsafeUrl("invalid_hostname") from exc
        host = HOSTNAME_ALIASES.get(host, host)
        # `localhost` is in BLOCKED_HOSTS for the public-internet path
        # (read-web, providers). The CDP transport (allow_loopback=True)
        # legitimately addresses the user's own browser by either an IP
        # literal or the `localhost` alias, so the block is lifted only
        # for that single hostname when loopback is permitted. Other
        # BLOCKED_HOSTS entries (metadata.google.internal, metadata,
        # metadata.azure.com) stay rejected regardless.
        if host in BLOCKED_HOSTS and not (allow_loopback and host == "localhost"):
            raise UnsafeUrl("blocked_host")
        # Reject hostnames that look like single-label localhost variants.
        if "." not in host and host not in {"x", "tools"} and not (
            allow_loopback and host == "localhost"
        ):
            # Single-label hosts (e.g., "router") are usually local-network.
            # Allow only if explicitly whitelisted.
            raise UnsafeUrl("single_label_host")
    # Percent-encode any non-ASCII bytes in path / query so the returned
    # URL is wire-safe (RFC 3986). Without this, a URL like
    # `https://example.com/café` produces a request line containing raw
    # `é` (0xE9), which the HTTP transport would either send unencoded
    # (illegal per RFC 3986) or crash on at the encode boundary
    # (UnicodeEncodeError if the wire encoding is ascii). `safe="..."` keeps
    # already-percent-encoded sequences intact (`%` is in the safe set, so
    # `%20` does NOT become `%2520`) and preserves the structural delimiters
    # the URL parser already split on (`/`, `?`, `=`, `&`, etc.).
    path = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=~")
    query = quote(parsed.query, safe="/%:@!$&'()*+,;=?")
    # Strip the URL fragment from the canonical form. Fragments NEVER go
    # on the wire (RFC 3986 §3.5 — fragment is processed client-side),
    # so the wire transport doesn't need it. Keeping it in the canonical
    # URL leaks in three places:
    #   1. **Cache keys**: `?#1` and `?#2` are the same wire resource but
    #      previously hashed to different keys, fragmenting the cache and
    #      letting an attacker bust it (and exhaust paid quotas) by varying
    #      the fragment.
    #   2. **Envelope.url + downstream consumers**: the URL in the envelope
    #      should represent what was fetched, not the user's UI anchor.
    #   3. **Approval scope matching**: an `exact:` scope with a fragment
    #      becomes brittle because match equality depends on it.
    # Audit input is handled separately: `sanitize_audit_input` receives
    # the RAW URL (with fragment) and applies the same query-secret
    # redaction to the fragment when fragment is parameter-shaped, so the
    # forensic record retains the SHAPE (a token-bearing fragment was
    # present) without leaking the values.
    #
    # Also strip the port from netloc when it's the default for the
    # scheme (Gemini #4 on commit 7ffd4c8). Without this,
    # `https://example.com/` and `https://example.com:443/` produce
    # different cache keys for the same wire resource — same class of
    # cache fragmentation as the fragment leak above. Validation already
    # restricts `port` to the default-for-scheme value when present, so
    # stripping is unconditional for known schemes.
    is_default_port = (
        (parsed.scheme == "http" and port == 80)
        or (parsed.scheme == "https" and port == 443)
    )
    if port is None or is_default_port:
        netloc = host
    else:
        netloc = f"{host}:{port}"
    return urlunsplit((parsed.scheme, netloc, path, query, ""))
