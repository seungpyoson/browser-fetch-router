from __future__ import annotations

import email.message
import http.client
import socket
import ssl
import sys
from dataclasses import dataclass, field
from typing import Callable, Iterable
from urllib.parse import parse_qsl, urlsplit

from browser_fetch_router.url_safety import (
    ResolvedTarget,
    UnsafeUrl,
    blocked_resolved_targets,
    is_blocked_ip,
    normalize_and_validate_url,
    validate_redirect,
)
from browser_fetch_router.url_safety import _parse_ip  # noqa: F401  (used in tests)

ACTION_PATH_PARTS = {
    "delete",
    "destroy",
    "unsubscribe",
    "confirm",
    "transfer",
    "purchase",
    "checkout",
    "logout",
    "remove",
    "cancel",
    "purge",
    "drop",
}
TOKEN_QUERY_NAMES = {
    "token",
    "confirm",
    "confirmation",
    "signature",
    "sig",
    "key",
    "code",
    "state",
    "nonce",
}
ACTION_QUERY_VALUES = {
    "delete",
    "destroy",
    "remove",
    "unsubscribe",
    "confirm",
    "transfer",
    "purchase",
    "checkout",
    "logout",
    "cancel",
}


@dataclass(frozen=True)
class SideEffectPolicy:
    strict: bool = False
    allow: bool = False


@dataclass(frozen=True)
class SafeResponse:
    url: str
    status_code: int
    text: str
    headers: dict[str, str] = field(default_factory=dict)


class ResponseTooLarge(RuntimeError):
    pass


class HostHeaderSmuggling(UnsafeUrl):
    pass


def _path_has_action(url: str) -> bool:
    parts = [part.lower() for part in urlsplit(url).path.split("/") if part]
    return any(part in ACTION_PATH_PARTS for part in parts)


def _query_has_one_time_token(url: str) -> bool:
    names = {name.lower() for name, _ in parse_qsl(urlsplit(url).query, keep_blank_values=True)}
    return bool(names & TOKEN_QUERY_NAMES)


def _query_has_action(url: str) -> bool:
    pairs = parse_qsl(urlsplit(url).query, keep_blank_values=True)
    return any(
        name.lower() in ACTION_QUERY_VALUES or value.lower() in ACTION_QUERY_VALUES
        for name, value in pairs
    )


def should_block_side_effect_redirect(url: str, policy: SideEffectPolicy) -> bool:
    if policy.allow:
        return False
    if policy.strict and (
        _path_has_action(url) or _query_has_action(url) or _query_has_one_time_token(url)
    ):
        return True
    return _path_has_action(url) and _query_has_one_time_token(url)


def side_effect_warning(url: str, policy: SideEffectPolicy) -> str | None:
    if policy.allow:
        return None
    if (
        _path_has_action(url)
        or _query_has_action(url)
        or _query_has_one_time_token(url)
    ):
        return "side_effect_like_url"
    return None


# --------- DNS-pinned HTTP transport -----------------------------------------


Resolver = Callable[[str], list[ResolvedTarget]]


def _default_resolver(hostname: str) -> list[ResolvedTarget]:
    """Resolve hostname via getaddrinfo and emit one ResolvedTarget per A/AAAA answer."""
    out: list[ResolvedTarget] = []
    try:
        infos = socket.getaddrinfo(
            hostname,
            None,
            type=socket.SOCK_STREAM,
            flags=socket.AI_ADDRCONFIG,
        )
    except socket.gaierror as exc:
        raise UnsafeUrl(f"dns_resolution_failed:{exc}") from exc
    for family, _socktype, _proto, _canonname, sockaddr in infos:
        ip = sockaddr[0]
        if family == socket.AF_INET:
            family_name = "AF_INET"
        elif family == socket.AF_INET6:
            family_name = "AF_INET6"
        else:
            family_name = str(family)
        out.append(ResolvedTarget(hostname=hostname, ip=ip, family=family_name))
    return out


_FORBIDDEN_HEADER_CHARS = ("\r", "\n", "\x00")


def _decode_with_charset(body: bytes, content_type: str) -> str:
    """Decode response body using the charset declared in `Content-Type`.

    Replaces the previous `if "charset=" in ctype: ctype.split(...)` parser,
    which mishandled three real-world cases: quoted values
    (`charset="utf-8"`), whitespace around `=` (`charset = utf-8`), and
    unknown codec names (`charset=utf-99` raised LookupError mid-decode).

    `email.message.Message.get_content_charset` is Python's stdlib MIME
    parameter parser (RFC 2045) and handles quoting, whitespace, and case
    correctly. The two known crash inputs are:

    - **Unknown codec name** (`charset=utf-99`) → `body.decode` raises
      `LookupError`. We fall back to UTF-8.
    - **Embedded NUL in Content-Type** (a malicious or buggy server sends
      `Content-Type: ...\x00...`) → `email.message.Message["Content-Type"]
      = ...` raises `ValueError("embedded null character")` BEFORE the
      decode step. We strip control bytes from the input and retry; if
      anything remains broken we still default to UTF-8 rather than
      letting the exception propagate as `internal_error` (exit 70).

    A malformed upstream Content-Type can never take down a request.
    """
    cleaned_ctype = (content_type or "application/octet-stream").translate(
        {b: None for b in (*range(0, 9), 11, 12, *range(14, 32), 127)}
    )
    try:
        msg = email.message.Message()
        msg["Content-Type"] = cleaned_ctype
        charset = (msg.get_content_charset() or "utf-8").strip() or "utf-8"
    except (ValueError, TypeError):
        charset = "utf-8"
    try:
        return body.decode(charset, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")

# Headers the transport layer owns. Caller-supplied versions of these can
# create request smuggling, framing desync, or routing confusion when they
# disagree with the transport's own framing. Reject them upfront rather than
# strip them silently — callers should not be passing these.
#
# Rule: any header the transport writes itself goes here, so the wire never
# carries a duplicate or a mismatched override. A future maintainer adding a
# transport-set header MUST also add it here; the regression test
# `test_every_transport_owned_header_rejects_caller_override` enforces this
# by attempting a caller override of every entry and asserting rejection.
TRANSPORT_OWNED_HEADERS = frozenset({
    "host",                # origin / routing — transport sets from validated hostname
    "content-length",      # body framing — transport sets from len(body)
    "transfer-encoding",   # body framing — TE+CL desync = HTTP smuggling
    "te",                  # transfer-encoding negotiation
    "trailer",             # chunked-encoding metadata
    "connection",          # keep-alive / upgrade — transport uses Connection: close
    "upgrade",             # protocol switch (e.g., websocket) — out of scope
    "expect",              # 100-continue — transport doesn't support
    "user-agent",          # transport sets a stable identifier; callers needing
                           # provider attribution must use a distinct X-* header
                           # like X-Caller-Identifier instead of overriding UA
})


# RFC 7230 §3.2.6 token character set: visible ASCII excluding the explicit
# delimiters listed in `tspecials`. Header NAMES must be tokens; header
# VALUES are field-content (visible ASCII + SP/HTAB as OWS). Treating both
# with the same rule was the round-2 bug — SP/HTAB in a name like
# `Transfer-Encoding ` (trailing space) bypassed the lower-cased lookup
# against TRANSPORT_OWNED_HEADERS, reintroducing the smuggling vector the
# set was meant to close.
_RFC7230_TOKEN_DELIMITERS = '"(),/:;<=>?@[\\]{}'
_RFC7230_TOKEN_CHARS = frozenset(
    chr(c) for c in range(0x21, 0x7F) if chr(c) not in _RFC7230_TOKEN_DELIMITERS
)


def _validate_extra_headers(headers: dict[str, str], expected_host: str) -> None:
    """Reject anything that would let a caller forge HTTP framing.

    Names and values have different RFC 7230 grammars and are validated
    separately:

    - **Name** must be `token` (RFC 7230 §3.2.6): visible ASCII excluding
      the `tspecials` delimiters. SP / HTAB are NOT allowed in names. This
      is the round-3 fix; previously SP/HTAB were tolerated everywhere,
      so a caller could pass `Transfer-Encoding ` (trailing space) and
      bypass the lower-cased lookup against TRANSPORT_OWNED_HEADERS,
      reintroducing the very smuggling vector the set was meant to close.
    - **Value** must be `field-content`: visible ASCII (0x21-0x7E) plus
      SP and HTAB as OWS. Non-ASCII bytes raise loudly at validation time
      so the wire encoder never sees them; RFC 8187 (`field*=UTF-8''...`)
      is the right escape hatch for genuinely non-ASCII metadata.
    - CR / LF / NUL are rejected wholesale in both name and value — this
      is the request-smuggling defense.
    - A caller-supplied transport-owned header (host, content-length,
      transfer-encoding, te, trailer, connection, upgrade, expect,
      user-agent) is rejected so the wire never carries duplicate or
      conflicting framing/identity headers.
    """
    if not headers:
        return
    for k, v in headers.items():
        # Smuggling guard FIRST so a CR/LF in the name doesn't even get a
        # chance to look like a token character.
        for ch in _FORBIDDEN_HEADER_CHARS:
            if ch in k or ch in v:
                raise HostHeaderSmuggling(
                    f"header_control_char_injection:{k!r}"
                )
        if not k:
            raise HostHeaderSmuggling("header_empty_name")
        # Name: RFC 7230 token. SP/HTAB explicitly NOT allowed.
        for ch in k:
            if ch not in _RFC7230_TOKEN_CHARS:
                raise HostHeaderSmuggling(
                    f"header_invalid_name_char:{k!r}"
                )
        # Value: visible ASCII + OWS (SP/HTAB).
        for ch in v:
            code = ord(ch)
            if code in (0x09, 0x20):
                continue
            if code < 0x21 or code > 0x7E:
                raise HostHeaderSmuggling(
                    f"header_non_ascii_byte:{k!r}"
                )
        kl = k.lower()
        if kl == "host":
            # Preserve the historical, more specific error code for the
            # Host-mismatch case so existing tests/diagnostics stay readable.
            if v.strip().lower() != expected_host.lower():
                raise HostHeaderSmuggling(f"host_header_smuggling:{v}")
            raise HostHeaderSmuggling("transport_owned_header:host")
        if kl in TRANSPORT_OWNED_HEADERS:
            # User-Agent gets a more helpful error so callers reaching for
            # provider attribution land on the right pattern.
            if kl == "user-agent":
                raise HostHeaderSmuggling(
                    "transport_owned_header:user-agent "
                    "(use X-Caller-Identifier for provider attribution)"
                )
            raise HostHeaderSmuggling(f"transport_owned_header:{kl}")


def _connect_pinned(
    ip: str,
    family: str,
    port: int,
    *,
    use_tls: bool,
    server_hostname: str,
    timeout: float,
    ssl_context: ssl.SSLContext | None = None,
    allow_loopback: bool = False,
) -> socket.socket:
    """Open a socket directly to the validated IP, then optionally wrap TLS
    using SNI = server_hostname (the original validated hostname). We never
    re-resolve; the IP came from the validated answer set.

    `allow_loopback=True` permits 127.0.0.0/8 / ::1 peers — used by the
    CDP transport. Other blocked categories still reject."""
    af = socket.AF_INET if family == "AF_INET" else socket.AF_INET6
    sock = socket.socket(af, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((ip, port))
    except OSError:
        sock.close()
        raise
    # Defensive check: peer address must be the IP we pinned.
    peer = sock.getpeername()[0]
    peer_obj = _parse_ip(peer)
    if peer_obj is None or is_blocked_ip(peer_obj, allow_loopback=allow_loopback):
        sock.close()
        raise UnsafeUrl("blocked_resolved_ip")
    if peer != ip:
        # Should not happen under POSIX but verify anyway.
        sock.close()
        raise UnsafeUrl("peer_address_mismatch")
    if not use_tls:
        return sock
    ctx = ssl_context or ssl.create_default_context()
    return ctx.wrap_socket(sock, server_hostname=server_hostname)


class SafeHttpClient:
    """DNS-pinning HTTP client.

    `resolver` and `connector` are injection points so tests can drive the
    pinning logic without real network access. In production they default to
    `getaddrinfo` and a stdlib socket connect."""

    def __init__(
        self,
        timeout: float = 30.0,
        *,
        resolver: Resolver | None = None,
        connector: Callable[..., socket.socket] | None = None,
        ssl_context: ssl.SSLContext | None = None,
        loopback_ok: bool = False,
    ) -> None:
        self.timeout = timeout
        self._resolver = resolver or _default_resolver
        self._connector = connector or _connect_pinned
        self._ssl_context = ssl_context
        # `loopback_ok=True` permits 127.0.0.0/8 / ::1 / `localhost` for
        # the entire request pipeline (URL canonicalization, DNS-resolved
        # target validation, peer-address check). Used by the CDP
        # transport whose intended path is the user's own browser. The
        # caller (cdp.fetch_tab_list) should also pass
        # `follow_redirects=False` so a compromised CDP server cannot
        # redirect /json to a public SSRF target.
        self._loopback_ok = bool(loopback_ok)

    def get_text(
        self,
        url: str,
        *,
        max_bytes: int = 10_000_000,
        side_effect_policy: SideEffectPolicy | None = None,
        extra_headers: dict[str, str] | None = None,
        follow_redirects: bool = True,
    ) -> SafeResponse:
        """Convenience wrapper for `request("GET", ...)`."""
        return self.request(
            "GET",
            url,
            max_bytes=max_bytes,
            side_effect_policy=side_effect_policy,
            extra_headers=extra_headers,
            follow_redirects=follow_redirects,
        )

    def request(
        self,
        method: str,
        url: str,
        *,
        body: bytes | str | None = None,
        max_bytes: int = 10_000_000,
        side_effect_policy: SideEffectPolicy | None = None,
        extra_headers: dict[str, str] | None = None,
        follow_redirects: bool = True,
    ) -> SafeResponse:
        """Issue an HTTP request through the DNS-pinning transport.

        Redirect handling:
        - 301, 302, 303 → coerce to GET and drop body (matches curl/browsers).
        - 307, 308 → preserve method and body.
        - Any redirect → strip `Authorization` header on cross-host redirect
          to avoid leaking credentials to a different origin.
        - `follow_redirects=False` raises `UnsafeUrl("unexpected_redirect")`
          on any 3xx — used by the CDP transport, where a redirect from
          the user's browser is never legitimate and would be a server
          compromise / proxy MITM signal. Closes the redirect-bypass
          class on every transport that pre-validates only the initial
          host but not redirect targets.
        """
        method = method.upper()
        if method not in {"GET", "POST", "PUT", "DELETE", "HEAD"}:
            raise UnsafeUrl(f"unsupported_method:{method}")
        side_effect_policy = side_effect_policy or SideEffectPolicy()
        current = normalize_and_validate_url(url, allow_loopback=self._loopback_ok)
        warning = side_effect_warning(current, side_effect_policy)
        if warning and side_effect_policy.strict:
            raise UnsafeUrl("side_effect_like_url")
        if warning:
            self._warn_stderr(warning, current)

        body_bytes: bytes | None = None
        if body is not None:
            body_bytes = body.encode("utf-8") if isinstance(body, str) else bytes(body)

        current_method = method
        current_body = body_bytes
        current_headers = dict(extra_headers or {})
        last_host = urlsplit(current).hostname or ""

        for _hop in range(10):
            response = self._do_one_request(
                current,
                method=current_method,
                body=current_body,
                extra_headers=current_headers,
                max_bytes=max_bytes,
            )
            if response.status_code not in {301, 302, 303, 307, 308}:
                return response
            # Refuse to silently follow a redirect when the caller
            # opted out. Used by the CDP transport: a real Chrome
            # DevTools server never redirects /json, so a 3xx is a
            # compromise / MITM signal and must surface as a SafetyError
            # rather than be transparently followed (the round-6 r6-01
            # CDP redirect-SSRF class).
            if not follow_redirects:
                raise UnsafeUrl(
                    f"unexpected_redirect:{response.status_code}"
                )
            # response.headers is built lowercased in _send_request_and_stream,
            # so a single "location" lookup is correct (no case fallback).
            location = response.headers.get("location")
            if not location:
                return response
            # Carry the transport's loopback policy through to redirect
            # validation. Without this, a SafeHttpClient(loopback_ok=True)
            # caller that follows redirects would reject a loopback
            # redirect target as blocked_ip — the caller's loopback
            # policy must be the same on the redirect chain as on the
            # initial URL.
            new_url = validate_redirect(
                current, location, allow_loopback=self._loopback_ok
            )
            if should_block_side_effect_redirect(new_url, side_effect_policy):
                raise UnsafeUrl("side_effect_redirect_blocked")

            # Cross-host check: strip Authorization on different-origin redirect.
            new_host = urlsplit(new_url).hostname or ""
            if new_host.lower() != last_host.lower():
                current_headers = {
                    k: v
                    for k, v in current_headers.items()
                    if k.lower() not in {"authorization", "cookie"}
                }
            last_host = new_host

            # Method/body coercion per redirect status.
            if response.status_code in {301, 302, 303}:
                current_method = "GET"
                current_body = None
                # Drop content-* headers since the body is gone.
                current_headers = {
                    k: v
                    for k, v in current_headers.items()
                    if not k.lower().startswith("content-")
                }
            # 307/308: preserve method and body unchanged.
            current = new_url
        raise UnsafeUrl("redirect_hop_limit")

    def _warn_stderr(self, code: str, url: str) -> None:
        print(f"[bfr] warning: {code} for {url}", file=sys.stderr)

    def _do_one_request(
        self,
        url: str,
        *,
        method: str,
        body: bytes | None,
        max_bytes: int,
        extra_headers: dict[str, str] | None,
    ) -> SafeResponse:
        parsed = urlsplit(url)
        hostname = parsed.hostname or ""
        if not hostname:
            raise UnsafeUrl("missing_host")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        targets = self._resolver(hostname)
        block_reason = blocked_resolved_targets(
            targets, allow_loopback=self._loopback_ok
        )
        if block_reason:
            raise UnsafeUrl(block_reason)
        # Pick the first answer; any could be used — all were validated.
        chosen = targets[0]
        _validate_extra_headers(extra_headers or {}, hostname)
        sock = self._connector(
            chosen.ip,
            chosen.family,
            port,
            use_tls=(parsed.scheme == "https"),
            server_hostname=hostname,
            timeout=self.timeout,
            ssl_context=self._ssl_context,
            allow_loopback=self._loopback_ok,
        )
        try:
            return self._send_request_and_stream(
                sock,
                method=method,
                hostname=hostname,
                port=port,
                path=(parsed.path or "/") + (("?" + parsed.query) if parsed.query else ""),
                extra_headers=extra_headers or {},
                max_bytes=max_bytes,
                scheme=parsed.scheme,
                body=body,
            )
        finally:
            try:
                sock.close()
            except Exception:
                pass

    def _send_request_and_stream(
        self,
        sock: socket.socket,
        *,
        method: str,
        hostname: str,
        port: int,
        path: str,
        extra_headers: dict[str, str],
        max_bytes: int,
        scheme: str,
        body: bytes | None = None,
    ) -> SafeResponse:
        # Use http.client over the pinned socket for HTTP/1.1 framing.
        host_header = hostname if (
            (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
        ) else f"{hostname}:{port}"
        # Build the request manually to keep streaming control.
        request_lines = [
            f"{method} {path} HTTP/1.1",
            f"Host: {host_header}",
            "User-Agent: browser-fetch-router/0.1",
            "Accept: */*",
            "Connection: close",
        ]
        if body is not None:
            request_lines.append(f"Content-Length: {len(body)}")
        # All transport-owned headers (host, content-length, transfer-encoding,
        # te, trailer, connection, upgrade, expect) were rejected by
        # _validate_extra_headers; we can iterate without filtering.
        for k, v in extra_headers.items():
            request_lines.append(f"{k}: {v}")
        request_lines.append("")
        request_lines.append("")
        # latin-1 (ISO-8859-1) is the encoding HTTP/1.1 historically permitted
        # for header field values per RFC 7230. The validator
        # `_validate_extra_headers` and `normalize_and_validate_url` both
        # restrict caller input to printable ASCII upstream, so under normal
        # operation no character above 0xFF reaches this encode call. The
        # try/except is defense-in-depth: if a future maintainer relaxes
        # validation OR an internal helper composes a non-ASCII string into
        # request_lines, the encoder failure becomes a structured
        # `header_non_ascii_byte` SafetyError (exit 4) instead of an
        # uncaught UnicodeEncodeError that the dispatcher would surface as
        # `internal_error` (exit 70) without correct attribution
        # (Gemini #1 on commit 7ffd4c8).
        try:
            wire_bytes = "\r\n".join(request_lines).encode("latin-1")
        except UnicodeEncodeError as exc:
            raise HostHeaderSmuggling(
                f"header_non_ascii_byte:wire_encode:{exc.reason}"
            ) from exc
        sock.sendall(wire_bytes)
        if body:
            sock.sendall(body)

        response = http.client.HTTPResponse(sock)
        try:
            try:
                response.begin()
            except http.client.HTTPException as exc:
                raise UnsafeUrl(f"http_protocol_error:{exc}") from exc
            headers = {k.lower(): v for k, v in response.getheaders()}
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = response.read(
                    min(64 * 1024, max(0, max_bytes - total + 1))
                )
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > max_bytes:
                    raise ResponseTooLarge("response_too_large")
            # Use a distinct local name from the request `body` parameter
            # to keep the function readable.
            response_body = b"".join(chunks)
            text = _decode_with_charset(
                response_body, headers.get("content-type", "")
            )
            return SafeResponse(
                url=f"{scheme}://{hostname}{':'+str(port) if port not in (80, 443) else ''}{path}",
                status_code=response.status,
                text=text,
                headers=headers,
            )
        finally:
            # Explicit close on every path. http.client.HTTPResponse
            # holds a reference to the socket file object and an
            # internal chunk-read state; without an explicit close it
            # is released only when GC runs, which a long-running CLI
            # process (`bfr serve`-style daemon use) would feel as a
            # gradual fd buildup (Gemini medium on commit 3b131b7).
            try:
                response.close()
            except Exception:
                pass


__all__ = [
    "SafeHttpClient",
    "SafeResponse",
    "SideEffectPolicy",
    "ResponseTooLarge",
    "HostHeaderSmuggling",
    "should_block_side_effect_redirect",
    "side_effect_warning",
]
