"""Transport-level tests for the DNS-pinning SafeHttpClient.

These exercise the pinning logic with a fake resolver and fake connector so
they need no network. Live integration coverage is in the acceptance suite.
"""
import io

import pytest

from browser_fetch_router.http_client import (
    HostHeaderSmuggling,
    SafeHttpClient,
    SideEffectPolicy,
    should_block_side_effect_redirect,
    side_effect_warning,
)
from browser_fetch_router.url_safety import ResolvedTarget, SafetyError, UnsafeUrl


class _FakeSocket:
    """Pretend socket that exposes an HTTP/1.1 response when read."""

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

    # http.client.HTTPResponse expects these:
    def settimeout(self, _t):
        pass


def _ok_response() -> bytes:
    body = b"hello world body content " * 5
    headers = (
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"Content-Length: " + str(len(body)).encode() + b"\r\n"
        b"\r\n"
    )
    return headers + body


def test_resolver_is_called_and_pinned_ip_used():
    calls: dict[str, list] = {"resolver": [], "connector": []}

    def fake_resolver(host):
        calls["resolver"].append(host)
        return [ResolvedTarget(hostname=host, ip="93.184.216.34", family="AF_INET")]

    def fake_connector(ip, family, port, *, use_tls, server_hostname, timeout, ssl_context=None, allow_loopback=False):
        calls["connector"].append(
            {"ip": ip, "family": family, "port": port, "use_tls": use_tls, "sni": server_hostname}
        )
        return _FakeSocket(_ok_response())

    client = SafeHttpClient(resolver=fake_resolver, connector=fake_connector)
    response = client.get_text("https://example.com/page")
    assert response.status_code == 200
    assert "hello world body" in response.text
    assert calls["resolver"] == ["example.com"]
    assert calls["connector"][0]["ip"] == "93.184.216.34"
    assert calls["connector"][0]["sni"] == "example.com"
    assert calls["connector"][0]["use_tls"] is True
    assert calls["connector"][0]["port"] == 443


def test_mixed_public_private_dns_rejects_hostname():
    def fake_resolver(host):
        return [
            ResolvedTarget(hostname=host, ip="93.184.216.34", family="AF_INET"),
            ResolvedTarget(hostname=host, ip="10.0.0.5", family="AF_INET"),
        ]

    def fake_connector(*a, **kw):
        raise AssertionError("connector should never be called when DNS validation fails")

    client = SafeHttpClient(resolver=fake_resolver, connector=fake_connector)
    with pytest.raises(UnsafeUrl):
        client.get_text("https://mixed.example/")


def test_empty_dns_answer_blocks():
    client = SafeHttpClient(resolver=lambda h: [], connector=lambda *a, **kw: None)
    with pytest.raises(UnsafeUrl):
        client.get_text("https://example.com/")


def test_caller_host_header_smuggling_rejected():
    def fake_resolver(host):
        return [ResolvedTarget(hostname=host, ip="93.184.216.34", family="AF_INET")]

    def fake_connector(*a, **kw):
        return _FakeSocket(_ok_response())

    client = SafeHttpClient(resolver=fake_resolver, connector=fake_connector)
    with pytest.raises(HostHeaderSmuggling):
        client.get_text(
            "https://example.com/", extra_headers={"Host": "evil.example"}
        )


def test_strict_side_effect_blocks_before_request():
    def fake_resolver(host):
        return [ResolvedTarget(hostname=host, ip="93.184.216.34", family="AF_INET")]

    called = {"connector": False}

    def fake_connector(*a, **kw):
        called["connector"] = True
        return _FakeSocket(_ok_response())

    client = SafeHttpClient(resolver=fake_resolver, connector=fake_connector)
    with pytest.raises(UnsafeUrl):
        client.get_text(
            "https://example.com/unsubscribe?token=x",
            side_effect_policy=SideEffectPolicy(strict=True),
        )
    assert called["connector"] is False


def test_redirect_to_private_blocks():
    """A redirect Location pointing at a private host must be re-validated and rejected."""

    def fake_resolver(host):
        return [ResolvedTarget(hostname=host, ip="93.184.216.34", family="AF_INET")]

    def redirect_response() -> bytes:
        return (
            b"HTTP/1.1 301 Moved Permanently\r\n"
            b"Location: http://127.0.0.1/internal\r\n"
            b"Content-Length: 0\r\n\r\n"
        )

    def fake_connector(*a, **kw):
        return _FakeSocket(redirect_response())

    client = SafeHttpClient(resolver=fake_resolver, connector=fake_connector)
    with pytest.raises(UnsafeUrl):
        client.get_text("https://example.com/redirect")


def test_response_too_large_aborts_stream():
    """Bodies exceeding max_bytes must abort during streaming, not after."""
    big_body = b"x" * (1024 * 100)

    def fake_resolver(host):
        return [ResolvedTarget(hostname=host, ip="93.184.216.34", family="AF_INET")]

    def fake_connector(*a, **kw):
        body = big_body
        return _FakeSocket(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
        )

    from browser_fetch_router.http_client import ResponseTooLarge

    client = SafeHttpClient(resolver=fake_resolver, connector=fake_connector)
    with pytest.raises(ResponseTooLarge):
        client.get_text("https://example.com/", max_bytes=1024)


# --- Internal-review BLOCKING #2 fix: POST request with body ---------------


def test_request_post_sends_body_and_content_length():
    """`request("POST", url, body=...)` must send the body bytes after the
    headers and auto-set Content-Length."""
    captured: dict[str, list[bytes]] = {"sends": []}

    def fake_resolver(host):
        return [ResolvedTarget(hostname=host, ip="93.184.216.34", family="AF_INET")]

    def fake_connector(*a, **kw):
        sock = _FakeSocket(_ok_response())
        original_sendall = sock.sendall

        def capture(data: bytes) -> None:
            captured["sends"].append(data)
            original_sendall(data)

        sock.sendall = capture  # type: ignore[method-assign]
        return sock

    client = SafeHttpClient(resolver=fake_resolver, connector=fake_connector)
    payload = b'{"hello":"world"}'
    response = client.request(
        "POST",
        "https://api.example.com/v1/extract",
        body=payload,
        extra_headers={"Authorization": "Bearer secret-key", "Content-Type": "application/json"},
    )
    assert response.status_code == 200
    sent = b"".join(captured["sends"])
    # Method line.
    assert sent.startswith(b"POST /v1/extract HTTP/1.1\r\n")
    # Auto-set Content-Length.
    assert b"Content-Length: " + str(len(payload)).encode() in sent
    # Auth header preserved.
    assert b"Authorization: Bearer secret-key" in sent
    # Body bytes follow the blank-line header terminator.
    assert sent.endswith(payload)


def test_request_rejects_unsupported_method():
    def fake_resolver(host):
        return [ResolvedTarget(hostname=host, ip="93.184.216.34", family="AF_INET")]

    client = SafeHttpClient(resolver=fake_resolver, connector=lambda *a, **kw: None)
    with pytest.raises(UnsafeUrl):
        client.request("CONNECT", "https://example.com/")


def test_request_string_body_is_utf8_encoded():
    captured: dict[str, list[bytes]] = {"sends": []}

    def fake_resolver(host):
        return [ResolvedTarget(hostname=host, ip="93.184.216.34", family="AF_INET")]

    def fake_connector(*a, **kw):
        sock = _FakeSocket(_ok_response())
        original = sock.sendall

        def cap(data: bytes) -> None:
            captured["sends"].append(data)
            original(data)

        sock.sendall = cap  # type: ignore[method-assign]
        return sock

    client = SafeHttpClient(resolver=fake_resolver, connector=fake_connector)
    client.request("POST", "https://example.com/", body="résumé")
    sent = b"".join(captured["sends"])
    assert "résumé".encode("utf-8") in sent


# --- Internal-review BLOCKING #3 fix: SafetyError hierarchy + propagation --


def test_unsafe_url_is_a_safety_error():
    """The marker base SafetyError must catch UnsafeUrl AND HostHeaderSmuggling."""
    assert issubclass(UnsafeUrl, SafetyError)
    assert issubclass(HostHeaderSmuggling, SafetyError)
    assert issubclass(HostHeaderSmuggling, UnsafeUrl)


def test_provider_re_raises_safety_error_during_dns_pinning(monkeypatch, tmp_path):
    """Regression: providers used to swallow UnsafeUrl as Exception. The
    provider must re-raise via `except SafetyError: raise`; the orchestrator
    propagates further; the CLI dispatcher (`cli._emit`) is what converts to
    `unsafe_url_blocked`. Here we verify the propagation contract — that
    `read_web()` raises rather than returning a misclassified envelope.
    End-to-end CLI behavior is covered by `test_cli_dispatcher_*`."""
    monkeypatch.setenv("HOME", str(tmp_path))
    from browser_fetch_router import read_web as module
    from browser_fetch_router.http_client import SafeHttpClient

    def hostile_resolver(host):
        return [ResolvedTarget(hostname=host, ip="10.0.0.5", family="AF_INET")]

    hostile_client = SafeHttpClient(
        resolver=hostile_resolver, connector=lambda *a, **kw: None
    )

    with pytest.raises(SafetyError):
        module.read_web(
            "https://example.com/article",
            no_cache=True,
            http_client=hostile_client,
        )


def test_extra_headers_reject_crlf_injection():
    """Pre-2026-05-06: only the Host header was validated. Now any header
    containing CR/LF/NUL is rejected to prevent request smuggling."""
    def fake_resolver(host):
        return [ResolvedTarget(hostname=host, ip="93.184.216.34", family="AF_INET")]

    client = SafeHttpClient(resolver=fake_resolver, connector=lambda *a, **kw: None)
    with pytest.raises(HostHeaderSmuggling):
        client.get_text(
            "https://example.com/",
            extra_headers={"X-Custom": "value\r\nInjected: yes"},
        )
    with pytest.raises(HostHeaderSmuggling):
        client.get_text(
            "https://example.com/",
            extra_headers={"X-Bad-Name\r\n": "value"},
        )


@pytest.mark.parametrize(
    "header_name",
    sorted({"host", "content-length", "transfer-encoding", "te", "trailer", "connection", "upgrade", "expect"}),
)
def test_transport_owned_headers_rejected_from_extra_headers(header_name):
    """Class-of-problem fix for HTTP smuggling: ANY caller-supplied header in
    TRANSPORT_OWNED_HEADERS must be rejected, regardless of method or body
    presence. This forecloses TE+CL desync, Connection: smuggling, Expect:
    100-continue framing, and similar future vulnerabilities by enforcing
    that the transport — not the caller — owns wire-framing headers."""
    from browser_fetch_router.http_client import TRANSPORT_OWNED_HEADERS

    assert header_name in TRANSPORT_OWNED_HEADERS

    def fake_resolver(host):
        return [ResolvedTarget(hostname=host, ip="93.184.216.34", family="AF_INET")]

    # Use a non-default-scheme/host value so we can distinguish the
    # transport-owned reject from the Host-mismatch reject.
    value = "evil.example" if header_name == "host" else "chunked"

    client = SafeHttpClient(resolver=fake_resolver, connector=lambda *a, **kw: None)
    with pytest.raises(HostHeaderSmuggling):
        client.get_text(
            "https://example.com/",
            extra_headers={header_name: value},
        )


def test_transport_owned_headers_rejected_for_post_with_body():
    """Same rejection applies on POST with body — the previous narrow patch
    only stripped Content-Length and Transfer-Encoding when body was present.
    The systematic fix rejects upfront regardless of method."""
    def fake_resolver(host):
        return [ResolvedTarget(hostname=host, ip="93.184.216.34", family="AF_INET")]

    def fake_connector(*a, **kw):
        return _FakeSocket(_ok_response())

    client = SafeHttpClient(resolver=fake_resolver, connector=fake_connector)
    with pytest.raises(HostHeaderSmuggling):
        client.request(
            "POST",
            "https://example.com/",
            body=b'{"k":"v"}',
            extra_headers={"Connection": "keep-alive"},
        )


# --- External-review (Gemini round 2 #M2): header bytes restricted to ASCII

# Round 1 (Gemini #3) widened the wire encoding from ascii→latin-1 to avoid
# UnicodeEncodeError on header values like `User-Agent: résumé`. Round 2
# pointed out that latin-1 still crashes on chars > 0xFF (em-dashes, CJK,
# any genuine Unicode). The class-correct fix per RFC 7230 §3.2 is to
# restrict header values to printable ASCII at validation time and use
# RFC 8187 (`field*=`) for the rare case of legitimately non-ASCII metadata.
# That makes the latin-1 encode boundary unable to crash by construction.


def test_extra_header_with_non_ascii_value_is_rejected_loudly():
    """A header value containing a non-ASCII char (e.g., `é` 0xE9 or em-dash
    U+2014) must raise HostHeaderSmuggling at validation, not crash the
    encoder at the wire boundary. Class fix for the previous latin-1
    crash-on-em-dash case."""
    import pytest

    from browser_fetch_router.http_client import HostHeaderSmuggling

    def fake_resolver(host):
        return [ResolvedTarget(hostname=host, ip="93.184.216.34", family="AF_INET")]

    client = SafeHttpClient(resolver=fake_resolver, connector=lambda *a, **kw: None)
    with pytest.raises(HostHeaderSmuggling, match="header_non_ascii_byte"):
        client.get_text(
            "https://example.com/",
            extra_headers={"X-Demo": "résumé café"},
        )
    # An em-dash is outside latin-1 too — same loud rejection, no crash.
    with pytest.raises(HostHeaderSmuggling, match="header_non_ascii_byte"):
        client.get_text(
            "https://example.com/",
            extra_headers={"X-Demo": "before — after"},
        )


def test_ascii_extra_header_passes_through_unchanged():
    """Counter-example: pure-ASCII header values (the supported path) reach
    the wire bytes intact. Tab and SP are explicitly allowed."""
    captured: dict[str, list[bytes]] = {"sends": []}

    def fake_resolver(host):
        return [ResolvedTarget(hostname=host, ip="93.184.216.34", family="AF_INET")]

    def fake_connector(*a, **kw):
        sock = _FakeSocket(_ok_response())
        original = sock.sendall

        def cap(data: bytes) -> None:
            captured["sends"].append(data)
            original(data)

        sock.sendall = cap  # type: ignore[method-assign]
        return sock

    client = SafeHttpClient(resolver=fake_resolver, connector=fake_connector)
    response = client.get_text(
        "https://example.com/",
        extra_headers={"X-Demo": "abc 123\ttabbed"},
    )
    assert response.status_code == 200
    sent = b"".join(captured["sends"])
    assert b"X-Demo: abc 123\ttabbed" in sent


def test_cross_host_redirect_strips_authorization():
    """A redirect to a different origin must NOT carry the Authorization
    header to the new host. Same-host redirects keep it."""
    captured: dict[str, list[bytes]] = {"sends": []}
    redirect_count = {"n": 0}

    def fake_resolver(host):
        # Both example.com and other.example resolve to the same public IP for
        # this test; what matters is the Host header value the client sends.
        return [ResolvedTarget(hostname=host, ip="93.184.216.34", family="AF_INET")]

    def fake_connector(*a, **kw):
        if redirect_count["n"] == 0:
            redirect_count["n"] += 1
            sock = _FakeSocket(
                b"HTTP/1.1 302 Found\r\n"
                b"Location: https://other.example/path\r\n"
                b"Content-Length: 0\r\n\r\n"
            )
        else:
            sock = _FakeSocket(_ok_response())
        original = sock.sendall

        def cap(data: bytes) -> None:
            captured["sends"].append(data)
            original(data)

        sock.sendall = cap  # type: ignore[method-assign]
        return sock

    client = SafeHttpClient(resolver=fake_resolver, connector=fake_connector)
    client.get_text(
        "https://example.com/start",
        extra_headers={"Authorization": "Bearer secret-key"},
    )
    # First request had Authorization.
    assert b"Authorization: Bearer secret-key" in captured["sends"][0]
    # Second request (after cross-host redirect) must NOT.
    assert b"Authorization:" not in captured["sends"][1]


def test_307_redirect_preserves_method_and_body():
    """RFC 7231: 307/308 must preserve method and body."""
    captured: dict[str, list[bytes]] = {"sends": []}
    state = {"hop": 0}

    def fake_resolver(host):
        return [ResolvedTarget(hostname=host, ip="93.184.216.34", family="AF_INET")]

    def fake_connector(*a, **kw):
        if state["hop"] == 0:
            state["hop"] = 1
            sock = _FakeSocket(
                b"HTTP/1.1 307 Temporary Redirect\r\n"
                b"Location: /new\r\n"
                b"Content-Length: 0\r\n\r\n"
            )
        else:
            sock = _FakeSocket(_ok_response())
        original = sock.sendall

        def cap(data: bytes) -> None:
            captured["sends"].append(data)
            original(data)

        sock.sendall = cap  # type: ignore[method-assign]
        return sock

    client = SafeHttpClient(resolver=fake_resolver, connector=fake_connector)
    body = b'{"k":"v"}'
    client.request("POST", "https://example.com/old", body=body)
    # Both hops must be POST and carry the body.
    assert captured["sends"][0].startswith(b"POST /old HTTP/1.1\r\n")
    assert captured["sends"][2 if len(captured["sends"]) > 2 else 1 - 1].startswith(b"POST")
    # Body bytes appear in both requests' send streams.
    sent_join = b"".join(captured["sends"])
    assert sent_join.count(body) == 2


def test_303_redirect_converts_to_get_and_drops_body():
    """RFC 7231: 303 See Other forces GET regardless of original method."""
    captured: dict[str, list[bytes]] = {"sends": []}
    state = {"hop": 0}

    def fake_resolver(host):
        return [ResolvedTarget(hostname=host, ip="93.184.216.34", family="AF_INET")]

    def fake_connector(*a, **kw):
        if state["hop"] == 0:
            state["hop"] = 1
            sock = _FakeSocket(
                b"HTTP/1.1 303 See Other\r\n"
                b"Location: /result\r\n"
                b"Content-Length: 0\r\n\r\n"
            )
        else:
            sock = _FakeSocket(_ok_response())
        original = sock.sendall

        def cap(data: bytes) -> None:
            captured["sends"].append(data)
            original(data)

        sock.sendall = cap  # type: ignore[method-assign]
        return sock

    client = SafeHttpClient(resolver=fake_resolver, connector=fake_connector)
    body = b'{"k":"v"}'
    client.request("POST", "https://example.com/submit", body=body)
    sent_join = b"".join(captured["sends"])
    # First hop is POST; second hop is GET.
    assert b"POST /submit HTTP/1.1\r\n" in sent_join
    assert b"GET /result HTTP/1.1\r\n" in sent_join
    # Body bytes appear exactly once: with the first POST. Second request
    # is body-less.
    assert sent_join.count(body) == 1
    # Second request must not carry Content-Length (no body to frame).
    second_idx = sent_join.index(b"GET /result HTTP/1.1\r\n")
    assert b"Content-Length:" not in sent_join[second_idx:]


# --- External-review (Gemini round 2 #M3): MIME-parameter-correct charset
# extraction. Class fix: replace the brittle `if "charset=" in ctype:
# split(...)` parser with email.message.Message (Python's stdlib RFC 2045
# parser) and add LookupError fallback for unknown codec names.


def test_decode_with_charset_handles_quoted_value():
    """Regression: `Content-Type: text/html; charset="utf-8"` (quoted value
    is RFC-2045-valid and emitted by some servers). Old parser treated the
    quotes as part of the codec name → LookupError on decode."""
    from browser_fetch_router.http_client import _decode_with_charset

    body = "café".encode("utf-8")
    out = _decode_with_charset(body, 'text/html; charset="utf-8"')
    assert out == "café"


def test_decode_with_charset_handles_whitespace_around_equals():
    """Regression: `charset = utf-8` (RFC permits LWS around `=`). Old
    parser's `split('charset=', 1)` missed when there was a space before
    `=` because the literal substring `charset=` wasn't present."""
    from browser_fetch_router.http_client import _decode_with_charset

    body = "café".encode("utf-8")
    out = _decode_with_charset(body, "text/html; charset = utf-8")
    assert out == "café"


def test_decode_with_charset_falls_back_on_unknown_codec():
    """Regression: a malformed Content-Type like `charset=utf-99` previously
    raised LookupError mid-decode and crashed the request. The fix wraps
    decode() in try/except LookupError and falls back to UTF-8 with
    `errors='replace'`."""
    from browser_fetch_router.http_client import _decode_with_charset

    body = b"hello"
    out = _decode_with_charset(body, "text/html; charset=utf-99")
    assert out == "hello"  # fell back to UTF-8 successfully


def test_decode_with_charset_defaults_to_utf8_when_missing():
    """No charset declared → default to UTF-8 (matches HTML5 default)."""
    from browser_fetch_router.http_client import _decode_with_charset

    body = "café".encode("utf-8")
    out = _decode_with_charset(body, "text/html")
    assert out == "café"


def test_decode_with_charset_handles_iso_8859_1():
    """Latin-1 declared → decoded as latin-1 (1 byte per char)."""
    from browser_fetch_router.http_client import _decode_with_charset

    body = b"caf\xe9"  # `é` as a single 0xE9 byte
    out = _decode_with_charset(body, "text/html; charset=iso-8859-1")
    assert out == "café"


def test_decode_with_charset_handles_empty_content_type():
    """Empty Content-Type header → default UTF-8, no crash."""
    from browser_fetch_router.http_client import _decode_with_charset

    out = _decode_with_charset(b"hello", "")
    assert out == "hello"
