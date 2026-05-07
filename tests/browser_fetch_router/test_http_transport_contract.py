"""HTTP transport subsystem contract suite.

Single source of truth for "safe outbound HTTP" in the
browser_fetch_router package. Every outbound HTTP request runs through
`SafeHttpClient.request()` (cli.py → providers/cdp/read_web →
SafeHttpClient → wire). This file enumerates the 17 invariants that
chain MUST satisfy and verifies each behaviorally.

Adding a new HTTP entry point:

  1. The new entry MUST go through `SafeHttpClient` (or be added to
     `paths.py`). The static guard `test_no_adhoc_http_transport` will
     fail the build if you import `urllib.request`,
     `http.client.HTTPConnection`, or `socket.create_connection`
     directly outside `http_client.py`.
  2. Run this suite — every applicable invariant runs against the new
     code path automatically (the suite tests the SafeHttpClient
     contract, not per-caller behavior).

Adding a new invariant:

  1. Document it in `docs/browser-fetch-router-http-transport-contract.md`.
  2. Add a parametrized test here.
  3. Run the suite — every existing entry inherits the new check.

Why this exists: PR #737 went through 15+ rounds of review. The
persistence subsystem closed via a similar contract (see
`test_persistence_contract.py`); this suite does the same for HTTP
transport. The systematic move: enumerate ALL invariants up front,
verify every code path satisfies all of them, in one closing pass.
After this, "new HTTP transport bug" requires either a missing
invariant in the enumeration (rare) or a non-transport bug class.
"""
from __future__ import annotations

import io
import re
from pathlib import Path

import pytest

from browser_fetch_router.http_client import (
    HostHeaderSmuggling,
    ResponseTooLarge,
    SafeHttpClient,
    SideEffectPolicy,
)
from browser_fetch_router.url_safety import (
    ResolvedTarget,
    UnsafeUrl,
)


# ============================================================
# Test fixtures — fake resolver + fake connector
# ============================================================


class _FakeSocket:
    """Minimal stand-in for an http.client-friendly socket.

    Produces the bytes given at construction when read; records bytes
    written via `sendall`. Used to drive SafeHttpClient end-to-end
    without opening real network connections.
    """

    def __init__(self, response_bytes: bytes):
        self._buf = io.BytesIO(response_bytes)
        self.sent: list[bytes] = []
        self.closed = False

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)

    def makefile(self, mode="rb", *args, **kwargs):
        return self._buf

    def recv(self, n):
        return self._buf.read(n)

    def close(self):
        self.closed = True

    def settimeout(self, _t):
        pass


def _ok_response(body: bytes = b"hello world") -> bytes:
    return (
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"Content-Length: " + str(len(body)).encode() + b"\r\n"
        b"Connection: close\r\n\r\n"
        + body
    )


def _redirect_response(status: int, location: str, body: bytes = b"") -> bytes:
    status_line = {
        301: b"301 Moved Permanently",
        302: b"302 Found",
        303: b"303 See Other",
        307: b"307 Temporary Redirect",
        308: b"308 Permanent Redirect",
    }[status]
    return (
        b"HTTP/1.1 " + status_line + b"\r\n"
        b"Location: " + location.encode() + b"\r\n"
        b"Content-Length: " + str(len(body)).encode() + b"\r\n"
        b"Connection: close\r\n\r\n"
        + body
    )


def _public_resolver(host):
    """Maps every host to the same public IP. Sufficient for invariant
    tests that don't need DNS-rebinding behavior."""
    return [ResolvedTarget(hostname=host, ip="93.184.216.34", family="AF_INET")]


def _make_client(*, loopback_ok=False, **kwargs) -> SafeHttpClient:
    """SafeHttpClient pre-wired with the public-IP resolver and a
    placeholder connector slot. Tests override `connector` to inject
    their adversarial response sequence."""
    return SafeHttpClient(
        resolver=_public_resolver,
        loopback_ok=loopback_ok,
        **kwargs,
    )


# ============================================================
# H1 — Method allowlist
# ============================================================


@pytest.mark.parametrize(
    "method", ["PATCH", "OPTIONS", "TRACE", "CONNECT", "FOO", "get post"]
)
def test_h1_method_allowlist_rejects_non_standard(method):
    """Invariant H1: only {GET, POST, PUT, DELETE, HEAD} accepted.
    Anything else raises `UnsafeUrl("unsupported_method:...")` BEFORE
    DNS resolution happens — no connection attempted."""
    client = _make_client(connector=lambda *a, **k: pytest.fail("connector hit"))
    with pytest.raises(UnsafeUrl, match="unsupported_method"):
        client.request(method, "https://example.com/")


# ============================================================
# H2 — URL normalized through normalize_and_validate_url
# ============================================================


@pytest.mark.parametrize(
    "blocked_url",
    [
        "http://169.254.169.254/latest/meta-data/",  # AWS IMDS
        "http://10.0.0.1/admin",                     # RFC 1918
        "http://192.168.1.1/",                       # RFC 1918
        "http://[::1]/",                             # IPv6 loopback
        "http://metadata.google.internal/",          # GCP metadata
        "ftp://example.com/",                        # blocked scheme
        "http://user:pass@example.com/",             # embedded creds
    ],
)
def test_h2_url_validation_blocks_ssrf_targets(blocked_url):
    """Invariant H2: every URL passes through `normalize_and_validate_url`
    BEFORE any DNS resolution. SSRF / scheme / credential rejections
    happen at the URL layer, not the network layer."""
    client = _make_client(connector=lambda *a, **k: pytest.fail("connector hit"))
    with pytest.raises(UnsafeUrl):
        client.request("GET", blocked_url)


# ============================================================
# H3 — DNS-resolved IPs validated per-answer
# ============================================================


def test_h3_dns_rebind_to_private_rejects_hostname():
    """Invariant H3: if a hostname resolves to BOTH public and private
    IPs, the request is rejected — no per-IP "first public answer wins"
    bypass. Closes round-3 DNS-rebinding class.
    """
    def mixed_resolver(host):
        return [
            ResolvedTarget(hostname=host, ip="93.184.216.34", family="AF_INET"),
            ResolvedTarget(hostname=host, ip="10.0.0.5", family="AF_INET"),
        ]

    client = SafeHttpClient(
        resolver=mixed_resolver,
        connector=lambda *a, **k: pytest.fail("connector hit"),
    )
    with pytest.raises(UnsafeUrl):
        client.request("GET", "https://example.com/")


# ============================================================
# H4 — follow_redirects=False raises on any 3xx
# ============================================================


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_h4_follow_redirects_false_rejects_every_3xx(status):
    """Invariant H4: when the caller opts out of redirect following,
    any 3xx status raises `UnsafeUrl("unexpected_redirect:<code>")`.
    Used by the CDP transport (a real Chrome DevTools server never
    redirects /json — a 3xx is a compromise / MITM signal).
    """
    def connector(*a, **k):
        return _FakeSocket(_redirect_response(status, "https://other.example/"))

    client = _make_client(connector=connector)
    with pytest.raises(UnsafeUrl, match=f"unexpected_redirect:{status}"):
        client.request("GET", "https://example.com/", follow_redirects=False)


# ============================================================
# H5 — Cross-host redirect strips Authorization + Cookie
# ============================================================


def test_h5_cross_host_redirect_strips_authorization_and_cookie():
    """Invariant H5: when a redirect crosses to a different host,
    Authorization AND Cookie headers are dropped from the follow-up
    request. Same-host redirects keep them.
    """
    captured: list[bytes] = []
    state = {"hop": 0}

    def connector(*a, **k):
        nonlocal state
        if state["hop"] == 0:
            state["hop"] = 1
            sock = _FakeSocket(
                _redirect_response(302, "https://other.example/path")
            )
        else:
            sock = _FakeSocket(_ok_response())
        original_sendall = sock.sendall

        def capturing_sendall(data: bytes) -> None:
            captured.append(data)
            original_sendall(data)

        sock.sendall = capturing_sendall
        return sock

    client = _make_client(connector=connector)
    client.get_text(
        "https://example.com/start",
        extra_headers={
            "Authorization": "Bearer secret-token-12345",
            "Cookie": "session=abc",
        },
    )
    assert b"Authorization:" in captured[0]
    assert b"Cookie:" in captured[0]
    # Cross-host hop must drop both.
    assert b"Authorization:" not in captured[1]
    assert b"Cookie:" not in captured[1]


# ============================================================
# H6 — Redirect hop limit
# ============================================================


def test_h6_redirect_hop_limit_caps_at_10():
    """Invariant H6: the redirect loop has a hard 10-hop ceiling.
    Past that, `UnsafeUrl("redirect_hop_limit")` raises rather than
    looping forever.
    """
    def connector(*a, **k):
        # Always redirect — would loop forever without the hop limit.
        return _FakeSocket(_redirect_response(302, "https://example.com/next"))

    client = _make_client(connector=connector)
    with pytest.raises(UnsafeUrl, match="redirect_hop_limit"):
        client.request("GET", "https://example.com/start")


# ============================================================
# H7 — 301/302/303 coerce to GET, drop body, drop Content-* headers
# ============================================================


@pytest.mark.parametrize("status", [301, 302, 303])
def test_h7_legacy_redirect_coerces_to_get_and_drops_body(status):
    """Invariant H7: 301/302/303 follow GET semantics — method becomes
    GET, body is dropped, Content-* headers are stripped (since the
    body is gone)."""
    # Per-hop capture: each connector call produces a new sublist
    # holding ALL bytes sent during that hop. http.client may sendall
    # the headers and body in separate calls, so concatenating per hop
    # is necessary before asserting on the request shape.
    hops: list[list[bytes]] = []

    def connector(*a, **k):
        if len(hops) == 0:
            sock = _FakeSocket(_redirect_response(status, "/destination"))
        else:
            sock = _FakeSocket(_ok_response())
        per_hop: list[bytes] = []
        hops.append(per_hop)
        original_sendall = sock.sendall

        def cap(data: bytes) -> None:
            per_hop.append(data)
            original_sendall(data)

        sock.sendall = cap
        return sock

    client = _make_client(connector=connector)
    client.request(
        "POST",
        "https://example.com/submit",
        body=b"original-body-bytes",
        extra_headers={"Content-Encoding": "gzip"},
    )
    hop0 = b"".join(hops[0])
    hop1 = b"".join(hops[1])
    # Hop 0 was POST with body.
    assert hop0.startswith(b"POST "), hop0[:80]
    assert b"original-body-bytes" in hop0
    # Hop 1 must be GET, no body, no Content-Encoding from the
    # original request (Content-Length is allowed if it's the
    # transport's own zero-body framing).
    assert hop1.startswith(b"GET "), hop1[:80]
    assert b"original-body-bytes" not in hop1
    assert b"Content-Encoding:" not in hop1


# ============================================================
# H8 — 307/308 preserve method and body
# ============================================================


@pytest.mark.parametrize("status", [307, 308])
def test_h8_modern_redirect_preserves_method_and_body(status):
    """Invariant H8: RFC 7231 — 307/308 preserve method and body."""
    hops: list[list[bytes]] = []

    def connector(*a, **k):
        if len(hops) == 0:
            sock = _FakeSocket(_redirect_response(status, "/destination"))
        else:
            sock = _FakeSocket(_ok_response())
        per_hop: list[bytes] = []
        hops.append(per_hop)
        original_sendall = sock.sendall

        def cap(data: bytes) -> None:
            per_hop.append(data)
            original_sendall(data)

        sock.sendall = cap
        return sock

    client = _make_client(connector=connector)
    client.request("POST", "https://example.com/submit", body=b"preserved-body")
    hop0 = b"".join(hops[0])
    hop1 = b"".join(hops[1])
    assert hop0.startswith(b"POST "), hop0[:80]
    assert hop1.startswith(b"POST "), hop1[:80]
    assert b"preserved-body" in hop1


# ============================================================
# H9 — Forbidden caller-supplied (transport-owned) headers
# ============================================================


@pytest.mark.parametrize(
    "header_name",
    [
        "Host",
        "Content-Length",
        "Transfer-Encoding",
        "TE",
        "Trailer",
        "Connection",
        "Upgrade",
        "Expect",
        # Casing variants — case-insensitive match required.
        "host",
        "CONTENT-LENGTH",
        "Transfer-encoding",
    ],
)
def test_h9_transport_owned_headers_rejected_from_caller(header_name):
    """Invariant H9: any header the transport writes itself is rejected
    when supplied by the caller. Caller-supplied versions create
    request smuggling, framing desync, or routing confusion. Match is
    case-insensitive (RFC 7230)."""
    client = _make_client(connector=lambda *a, **k: pytest.fail("connector hit"))
    with pytest.raises(HostHeaderSmuggling):
        client.request(
            "GET",
            "https://example.com/",
            extra_headers={header_name: "anything"},
        )


# ============================================================
# H10 — Header name + value byte validation
# ============================================================


@pytest.mark.parametrize(
    "name,value",
    [
        # CR / LF / NUL anywhere in name or value
        ("X-Foo", "bad\r\nInjected: header"),
        ("X-Foo", "bad\nInjected: header"),
        ("X-Foo", "bad\rInjected: header"),
        ("X-Foo", "value\x00with\x00nul"),
        ("X-Bad\r\nInjection", "value"),
        ("X-Bad\x00Header", "value"),
        # Non-ASCII in header value
        ("X-Foo", "café"),
        # Non-token in header name (RFC 7230 token delimiter)
        ("X Bad Spaces", "value"),
        ("X(parens)", "value"),
    ],
)
def test_h10_header_name_and_value_bytes_validated(name, value):
    """Invariant H10: caller-supplied header names must be RFC 7230
    tokens; values must be visible ASCII + SP/HTAB. Any CR/LF/NUL or
    non-ASCII byte raises `HostHeaderSmuggling`."""
    client = _make_client(connector=lambda *a, **k: pytest.fail("connector hit"))
    with pytest.raises(HostHeaderSmuggling):
        client.request(
            "GET",
            "https://example.com/",
            extra_headers={name: value},
        )


# ============================================================
# H11 — Response size cap enforced pre-decompression
# ============================================================


def test_h11_response_size_cap_aborts_stream():
    """Invariant H11: when the response body exceeds `max_bytes`,
    the read loop aborts mid-stream and `ResponseTooLarge` raises.
    Cap is enforced on RAW bytes (pre-decompression) so a gzip bomb
    cannot expand past the limit before we notice."""
    big_body = b"x" * 1_000_000  # 1 MB

    def connector(*a, **k):
        return _FakeSocket(_ok_response(big_body))

    client = _make_client(connector=connector)
    with pytest.raises(ResponseTooLarge):
        client.get_text("https://example.com/", max_bytes=10_000)


# ============================================================
# H12 — Connection close in finally
# ============================================================


def test_h12_socket_closed_even_when_response_is_too_large():
    """Invariant H12: an exception during streaming MUST NOT leak the
    socket. The `finally` block in `_send_request_and_stream` closes
    both the http.client response and the socket."""
    big_body = b"x" * 1_000_000
    captured_sockets: list[_FakeSocket] = []

    def connector(*a, **k):
        sock = _FakeSocket(_ok_response(big_body))
        captured_sockets.append(sock)
        return sock

    client = _make_client(connector=connector)
    with pytest.raises(ResponseTooLarge):
        client.get_text("https://example.com/", max_bytes=10_000)

    assert captured_sockets, "connector never called"
    assert all(s.closed for s in captured_sockets), (
        "exception during streaming leaked an open socket"
    )


# ============================================================
# H13 — Single timeout applied
# ============================================================


def test_h13_timeout_propagates_to_connector():
    """Invariant H13: SafeHttpClient stores the constructor `timeout`
    and propagates it to the connector that opens the socket. Without
    this, requests could hang indefinitely on a slow/unreachable host.
    Captured directly: assert (a) `client.timeout == 7.5` after init,
    and (b) the connector receives `timeout=7.5` in its kwargs."""
    captured_kwargs: list[dict] = []

    def connector(*args, **kwargs):
        captured_kwargs.append(kwargs)
        return _FakeSocket(_ok_response())

    client = SafeHttpClient(
        resolver=_public_resolver, connector=connector, timeout=7.5
    )
    assert client.timeout == 7.5, (
        f"SafeHttpClient.timeout attribute mismatch: {client.timeout}"
    )
    client.get_text("https://example.com/")
    assert captured_kwargs, "connector never called"
    assert captured_kwargs[0].get("timeout") == 7.5, (
        f"connector did not receive timeout=7.5; got "
        f"{captured_kwargs[0].get('timeout')!r}. SafeHttpClient must "
        "propagate the constructor timeout into the socket layer or "
        "every request can hang indefinitely."
    )


# ============================================================
# H14 — TLS uses ssl.create_default_context (verified)
# ============================================================


def test_h14_default_ssl_context_does_full_verification():
    """Invariant H14: when no ssl_context is supplied, the default is
    `ssl.create_default_context()`, which enables hostname checking
    AND certificate verification. Static check on the source — we
    don't actually open a TLS connection here."""
    import inspect
    from browser_fetch_router import http_client

    src = inspect.getsource(http_client)
    # The default-context creation MUST be present somewhere in the
    # connect path. Custom contexts created with `ssl._create_unverified_context()`
    # or `verify_mode = CERT_NONE` would defeat verification.
    assert "ssl.create_default_context" in src, (
        "http_client must default to ssl.create_default_context() — "
        "explicit verify_mode=CERT_NONE or _create_unverified_context() "
        "would silently disable cert validation"
    )
    assert "_create_unverified_context" not in src, (
        "http_client uses ssl._create_unverified_context — TLS verification "
        "is disabled. This is never the right answer in production."
    )
    assert "CERT_NONE" not in src, (
        "http_client references CERT_NONE — TLS verification disabled."
    )


# ============================================================
# H15 — Embedded credentials in URL rejected
# ============================================================


@pytest.mark.parametrize(
    "creds_url",
    [
        "https://user:pass@example.com/",
        "https://user@example.com/",
        "http://admin:hunter2@internal.test/",
    ],
)
def test_h15_embedded_credentials_rejected(creds_url):
    """Invariant H15: `https://user:pass@host/` and any embedded
    credential form is rejected at URL validation. Sending creds via
    URL is a phishing / leakage hazard."""
    client = _make_client(connector=lambda *a, **k: pytest.fail("connector hit"))
    with pytest.raises(UnsafeUrl):
        client.request("GET", creds_url)


# ============================================================
# H16 — Control bytes in URL rejected
# ============================================================


@pytest.mark.parametrize(
    "url_with_control",
    [
        "http://example.com/path\r\nX-Injected: bad",
        "http://example.com/path\nX-Injected: bad",
        "http://example.com/path\rX-Injected: bad",
        "http://example.com/p\x00ath",
        "http://example.com/p\x01ath",
        "http://example.com/p\x1fath",
        "http://example.com/p\x7fath",  # DEL
    ],
)
def test_h16_control_bytes_in_url_rejected(url_with_control):
    """Invariant H16: any C0 (0x00-0x1F) or C1 (0x7F-0x9F) control byte
    in the URL string raises before urlsplit. Closes header/request-
    line injection class via crafted URLs."""
    client = _make_client(connector=lambda *a, **k: pytest.fail("connector hit"))
    with pytest.raises(UnsafeUrl):
        client.request("GET", url_with_control)


# ============================================================
# H17 — Side-effect policy asymmetry
# ============================================================


def test_h17_strict_side_effect_policy_blocks_initial_url():
    """Invariant H17a: in strict mode, the initial URL itself is
    blocked when it has side-effect signals (action path, action
    query, one-time token)."""
    client = _make_client(connector=lambda *a, **k: pytest.fail("connector hit"))
    with pytest.raises(UnsafeUrl, match="side_effect_like_url"):
        client.request(
            "GET",
            "https://example.com/confirm?token=one-time-secret-12345",
            side_effect_policy=SideEffectPolicy(strict=True),
        )


def test_h17_redirect_to_side_effect_url_blocked_even_in_default_mode():
    """Invariant H17b: even in DEFAULT (non-strict) mode, a redirect
    target that combines a path action AND a one-time token is
    blocked. Server-controlled redirect targets are held to a higher
    bar than user-typed initial URLs."""
    def connector(*a, **k):
        return _FakeSocket(
            _redirect_response(
                302,
                "https://example.com/confirm/action?token=one-time-secret-99",
            )
        )

    client = _make_client(connector=connector)
    with pytest.raises(UnsafeUrl, match="side_effect_redirect_blocked"):
        client.request("GET", "https://example.com/safe")


# ============================================================
# H17 (closing-pass) — validate_redirect carries allow_loopback
# ============================================================


def test_h17_validate_redirect_carries_loopback_policy():
    """Closing-pass invariant: `validate_redirect` MUST receive the
    caller's `allow_loopback` policy so the redirect chain has the
    same loopback semantics as the initial URL.

    Pre-fix this was a hidden coupling: any caller of
    `SafeHttpClient(loopback_ok=True)` had to ALSO use
    `follow_redirects=False`, otherwise a redirect from a loopback-
    permitted initial URL would be rejected as `blocked_ip` even
    though the transport intended to permit loopback. Closing the
    coupling structurally lets future callers pair the flags as
    needed.
    """
    import inspect
    from browser_fetch_router import http_client, url_safety

    # The function signature must accept allow_loopback explicitly.
    sig = inspect.signature(url_safety.validate_redirect)
    assert "allow_loopback" in sig.parameters, (
        "validate_redirect must accept allow_loopback so the redirect "
        "chain inherits the caller's transport loopback policy"
    )

    # The caller (SafeHttpClient.request) must pass it.
    src = inspect.getsource(http_client.SafeHttpClient.request)
    assert "allow_loopback=self._loopback_ok" in src, (
        "SafeHttpClient.request must propagate self._loopback_ok to "
        "validate_redirect — without this, a loopback_ok=True caller "
        "that follows redirects would reject loopback redirect targets"
    )


# ============================================================
# Static guard — no ad-hoc HTTP transport
# ============================================================


# --- HTTP transport static-guard banned sets -----------------------------
#
# Hoisted to module level so the round-17 reproduction tests can scan
# synthetic offender files using the same banned-set source of truth.
# Updating any set automatically tightens the production guard AND every
# round-17 reproduction that exercises it. Do not duplicate.
#
# Three families of bypass are caught — Class-B round-17 closure:
#   1. Top-level `import M` for whole-module bans (urllib.request,
#      requests). `socket` and `http.client` are NOT banned at this
#      level because they have legitimate non-HTTP uses (`socket.
#      getaddrinfo` for read-only DNS in `cdp.py`).
#   2. `from M import N` for per-name bans inside multi-purpose
#      modules. `from socket import getaddrinfo` is allowed; `from
#      socket import create_connection` is not. Same shape for
#      http.client. The wildcard key `*` means "any name from M".
#   3. Attribute-call chains like `urllib.request.urlopen(...)` or
#      `socket.socket()` for callers using the dotted form.

HTTP_BANNED_TOPLEVEL_IMPORTS = {
    "urllib.request": "urllib.request",
    "requests": "requests (third-party)",
}

HTTP_BANNED_IMPORT_FROM_NAMES = {
    "urllib.request": {"*": "urllib.request (any name)"},
    "requests": {"*": "requests (third-party)"},
    "http.client": {
        "HTTPConnection": "http.client.HTTPConnection",
        "HTTPSConnection": "http.client.HTTPSConnection",
    },
    "socket": {
        "create_connection": "socket.create_connection",
        "socket": "socket.socket (raw constructor)",
        "socketpair": "socket.socketpair (raw constructor)",
    },
}

HTTP_BANNED_ATTRIBUTE_CHAINS = {
    ("urllib", "request", "urlopen"): "urllib.request.urlopen",
    ("urllib", "request", "Request"): "urllib.request.Request",
    ("http", "client", "HTTPConnection"): "http.client.HTTPConnection",
    ("http", "client", "HTTPSConnection"): "http.client.HTTPSConnection",
    ("socket", "create_connection"): "socket.create_connection",
    # Class-B round-17: the raw constructor lets a future caller do
    # `s = socket.socket(); s.connect(...)` to bypass create_connection.
    ("socket", "socket"): "socket.socket (raw constructor)",
    ("socket", "socketpair"): "socket.socketpair (raw constructor)",
}


def _http_attribute_chain(node):
    """Build the full dotted name from an Attribute access chain.

    For `urllib.request.urlopen` the AST is:
      Attribute(attr='urlopen',
        value=Attribute(attr='request',
          value=Name(id='urllib')))
    Walks back to the Name root, returns the dotted parts in order.
    """
    import ast

    parts: list[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        return tuple(reversed(parts))
    return None


def find_http_transport_offenders(pkg_root: Path, *, transport_module_name: str = "http_client.py") -> list[str]:
    """Scan a package directory for HTTP-transport contract bypasses.

    Module-level so reproduction tests in `test_round17_replication.py`
    can invoke it on a synthetic offender directory. Production guard
    below calls it with the real `browser_fetch_router/` path.
    """
    import ast

    transport_module = (pkg_root / transport_module_name).resolve()
    offenders: list[str] = []
    for py in pkg_root.rglob("*.py"):
        if py.resolve() == transport_module:
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        rel = py.relative_to(pkg_root.parent) if py.is_relative_to(pkg_root.parent) else py
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                # Per-name banning lets `from socket import getaddrinfo`
                # through while catching `from socket import create_connection`
                # (and the constructor `from socket import socket`).
                banned_names = HTTP_BANNED_IMPORT_FROM_NAMES.get(node.module or "")
                if banned_names:
                    if "*" in banned_names:
                        offenders.append(
                            f"{rel}:{node.lineno} from {node.module} import "
                            f"({banned_names['*']})"
                        )
                    else:
                        for alias in node.names:
                            if alias.name in banned_names:
                                offenders.append(
                                    f"{rel}:{node.lineno} from {node.module} "
                                    f"import {alias.name} "
                                    f"({banned_names[alias.name]})"
                                )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in HTTP_BANNED_TOPLEVEL_IMPORTS:
                        offenders.append(
                            f"{rel}:{node.lineno} import {alias.name} "
                            f"({HTTP_BANNED_TOPLEVEL_IMPORTS[alias.name]})"
                        )
            elif isinstance(node, ast.Attribute):
                chain = _http_attribute_chain(node)
                if chain in HTTP_BANNED_ATTRIBUTE_CHAINS:
                    offenders.append(
                        f"{rel}:{node.lineno} {HTTP_BANNED_ATTRIBUTE_CHAINS[chain]} "
                        "— must route through SafeHttpClient"
                    )
    return offenders


def test_no_adhoc_http_transport_in_production_code():
    """Class-level static guard: production code MUST NOT use raw
    HTTP/socket primitives outside `http_client.py`. AST-based scan
    so docstring text mentioning urllib/http.client doesn't
    false-positive. Banned set lives at module-level
    (`HTTP_BANNED_IMPORT_MODULES` / `HTTP_BANNED_ATTRIBUTE_CHAINS`)
    so reproduction tests share the same source of truth.

    A new HTTP entry point added without going through SafeHttpClient
    trips this test instead of silently re-introducing an SSRF surface.
    """
    pkg = Path(__file__).resolve().parents[2] / "browser_fetch_router"
    offenders = find_http_transport_offenders(pkg)
    assert not offenders, (
        "Ad-hoc HTTP transport detected — bypass of SafeHttpClient. "
        "See docs/browser-fetch-router-http-transport-contract.md.\n"
        + "\n".join(offenders)
    )
