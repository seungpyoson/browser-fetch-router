import base64
import json

import pytest

from browser_fetch_router.cdp import cdp_base_url


def test_cdp_defaults_to_loopback(monkeypatch):
    monkeypatch.delenv("BFR_CDP_URL", raising=False)
    assert cdp_base_url() == "http://127.0.0.1:9222"


def test_remote_cdp_rejected_without_flag(monkeypatch):
    monkeypatch.setenv("BFR_CDP_URL", "http://example.com:9222")
    assert cdp_base_url(allow_remote=False) is None


def test_remote_cdp_allowed_with_flag(monkeypatch):
    monkeypatch.setenv("BFR_CDP_URL", "http://example.com:9222")
    assert cdp_base_url(allow_remote=True) == "http://example.com:9222"


def test_localhost_alias_accepted(monkeypatch):
    monkeypatch.setenv("BFR_CDP_URL", "http://localhost:9222/")
    assert cdp_base_url() == "http://localhost:9222"


# --- Task 14 helpers --------------------------------------------------------


def test_list_redacts_default_deny_tab():
    from browser_fetch_router.read_user_tabs import redact_tab_list

    tabs = [
        {"id": "1", "title": "Inbox", "url": "https://mail.google.com/mail/u/0", "type": "page"},
    ]
    redacted = redact_tab_list(tabs, show_all=False)
    assert redacted[0]["title"] == "[hidden]"
    assert redacted[0]["url"] == "[hidden]"
    assert redacted[0]["redacted"] is True


def test_list_show_all_does_not_unredact_default_denied():
    """Round-5 fix (Greptile #2 on commit 7ffd4c8): `show_all` no
    longer bypasses default-deny redaction. The flag was previously a
    coarse "I'm authorized" signal that doubled as an authorization
    bypass for sensitive hosts (mail.google.com, 1password.com, etc.).
    To see a default-denied URL, callers now use `read-tab` with an
    explicit `exact:` per-URL approval — the proper auth path."""
    from browser_fetch_router.read_user_tabs import redact_tab_list

    tabs = [{"id": "1", "title": "Inbox", "url": "https://mail.google.com/mail/u/0", "type": "page"}]
    redacted = redact_tab_list(tabs, show_all=True)
    assert redacted[0]["title"] == "[hidden]"
    assert redacted[0]["url"] == "[hidden]"
    assert redacted[0]["redacted"] is True


def test_list_show_all_does_not_redact_neutral_url():
    """Counter-example: a non-default-denied URL is unaffected by
    show_all (no redaction either way for neutral hosts)."""
    from browser_fetch_router.read_user_tabs import redact_tab_list

    tabs = [{"id": "1", "title": "HN", "url": "https://news.ycombinator.com/", "type": "page"}]
    redacted = redact_tab_list(tabs, show_all=True)
    assert redacted[0]["title"] == "HN"
    assert redacted[0]["url"] == "https://news.ycombinator.com/"
    assert redacted[0]["redacted"] is False


def test_content_cap_adds_truncation_marker():
    from browser_fetch_router.read_user_tabs import cap_content

    assert cap_content("abcdef", 3) == "abc\n\n[TRUNCATED after 3 chars]"


def test_content_cap_passes_through_short_text():
    from browser_fetch_router.read_user_tabs import cap_content

    assert cap_content("hi", 100) == "hi"


# --- Internal-review WARNING #2 fix: drop substring fallback ---------------


def test_resolve_tab_active_returns_first_page_tab():
    from browser_fetch_router.read_user_tabs import _resolve_tab

    tabs = [
        {"id": "BG", "title": "Background", "url": "chrome://background", "type": "background_page"},
        {"id": "T1", "title": "Hacker News", "url": "https://news.ycombinator.com/", "type": "page"},
    ]
    assert _resolve_tab("active", tabs) == tabs[1]


def test_resolve_tab_exact_id_or_url_matches():
    from browser_fetch_router.read_user_tabs import _resolve_tab

    tabs = [
        {"id": "T1", "title": "Hacker News", "url": "https://news.ycombinator.com/", "type": "page"},
        {"id": "T2", "title": "Reddit", "url": "https://www.reddit.com/", "type": "page"},
    ]
    assert _resolve_tab("T2", tabs) == tabs[1]
    assert _resolve_tab("https://www.reddit.com/", tabs) == tabs[1]


def test_resolve_tab_substring_no_longer_matches():
    """Regression: title-substring fallback was removed because a target like
    "x" silently routed to any tab containing the letter."""
    from browser_fetch_router.read_user_tabs import _resolve_tab

    tabs = [
        {"id": "T1", "title": "Hacker News", "url": "https://news.ycombinator.com/", "type": "page"},
        {"id": "T2", "title": "Reddit", "url": "https://www.reddit.com/", "type": "page"},
    ]
    # "Reddit" used to match Reddit by title-substring. Now must return None.
    assert _resolve_tab("Reddit", tabs) is None
    # Single-letter target used to match the first tab containing it.
    assert _resolve_tab("x", tabs) is None


# --- Internal-review SUGGESTION fix: CDP response size cap -----------------


def _start_loopback_cdp(handler):
    """Spawn a real loopback HTTP server on an ephemeral port. CDP fetch
    is exercised end-to-end through SafeHttpClient (DNS-pinned, redirect-
    rejected); the previous monkeypatch-on-urlopen approach no longer
    applies because fetch_tab_list does not call urlopen."""
    from http.server import HTTPServer
    import threading

    server = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def test_fetch_tab_list_aborts_on_oversized_response():
    """A malicious or runaway CDP endpoint must not be able to OOM the CLI.
    SafeHttpClient streams up to MAX_CDP_RESPONSE_BYTES; above that
    `ResponseTooLarge` propagates and fetch_tab_list converts to
    `CdpResponseTooLarge`."""
    from http.server import BaseHTTPRequestHandler

    import pytest

    from browser_fetch_router import cdp as cdp_module

    huge_payload = b"x" * (cdp_module.MAX_CDP_RESPONSE_BYTES + 1024)

    class _Huge(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(huge_payload)))
            self.end_headers()
            self.wfile.write(huge_payload)

        def log_message(self, *_a):
            pass

    server = _start_loopback_cdp(_Huge)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        with pytest.raises(cdp_module.CdpResponseTooLarge):
            cdp_module.fetch_tab_list(base)
    finally:
        server.shutdown()


def test_fetch_tab_list_succeeds_under_cap():
    """A normal-sized CDP response decodes fine through SafeHttpClient."""
    import json
    from http.server import BaseHTTPRequestHandler

    from browser_fetch_router import cdp as cdp_module

    payload = json.dumps([
        {"id": "T1", "title": "x", "url": "https://example.com/",
         "type": "page", "webSocketDebuggerUrl": "ws://x"},
    ]).encode("utf-8")

    class _Ok(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_a):
            pass

    server = _start_loopback_cdp(_Ok)
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        tabs = cdp_module.fetch_tab_list(base)
        assert tabs == [
            {
                "id": "T1",
                "title": "x",
                "url": "https://example.com/",
                "type": "page",
                "webSocketDebuggerUrl": "ws://x",
            }
        ]
    finally:
        server.shutdown()


def test_validate_tab_websocket_url_accepts_matching_loopback_target():
    from browser_fetch_router import cdp as cdp_module

    assert (
        cdp_module.validate_tab_websocket_url(
            "ws://127.0.0.1:9222/devtools/page/T1",
            "http://127.0.0.1:9222",
        )
        == "ws://127.0.0.1:9222/devtools/page/T1"
    )


@pytest.mark.parametrize(
    ("ws_url", "base_url", "exc_type"),
    [
        ("", "http://127.0.0.1:9222", "CdpWebSocketUrlInvalid"),
        ("http://127.0.0.1:9222/devtools/page/T1", "http://127.0.0.1:9222", "CdpWebSocketUrlInvalid"),
        ("ws://user:pass@127.0.0.1:9222/devtools/page/T1", "http://127.0.0.1:9222", "CdpWebSocketUrlInvalid"),
        ("ws://127.0.0.1:notaport/devtools/page/T1", "http://127.0.0.1:9222", "CdpWebSocketUrlInvalid"),
        ("ws://example.com:9222/devtools/page/T1", "http://127.0.0.1:9222", "CdpWebSocketUrlMismatch"),
        ("ws://127.0.0.1:9333/devtools/page/T1", "http://127.0.0.1:9222", "CdpWebSocketUrlMismatch"),
        ("ws://127.0.0.1:9222/devtools/page/T1", "http://127.0.0.1:notaport", "CdpWebSocketUrlMismatch"),
        ("wss://127.0.0.1:9222/devtools/page/T1", "http://127.0.0.1:9222", "CdpWebSocketUrlMismatch"),
    ],
)
def test_validate_tab_websocket_url_rejects_unsafe_or_mismatched_targets(ws_url, base_url, exc_type):
    from browser_fetch_router import cdp as cdp_module

    with pytest.raises(getattr(cdp_module, exc_type)):
        cdp_module.validate_tab_websocket_url(ws_url, base_url)


class _FakeWebSocket:
    def __init__(self, responses):
        self.responses = list(responses)
        self.sent = []

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def send(self, payload):
        self.sent.append(json.loads(payload))

    def recv(self):
        return json.dumps(self.responses.pop(0))


def test_cdp_drain_limit_is_named_for_reviewable_tuning():
    from browser_fetch_router import cdp as cdp_module

    assert cdp_module._CDP_MAX_DRAIN_MESSAGES == 100


def test_send_cdp_command_maps_runtime_exception_details_to_protocol_error():
    from browser_fetch_router import cdp as cdp_module

    socket = _FakeWebSocket(
        [
            {
                "id": 4,
                "result": {
                    "exceptionDetails": {
                        "text": "Uncaught",
                    }
                },
            },
        ]
    )

    with pytest.raises(cdp_module.CdpProtocolError, match="Runtime.evaluate failed"):
        cdp_module._send_cdp_command(socket, 4, "Runtime.evaluate")


def test_fetch_tab_text_uses_isolated_world(monkeypatch):
    from browser_fetch_router import cdp as cdp_module

    socket = _FakeWebSocket(
        [
            {"id": 1, "result": {}},
            {
                "id": 2,
                "result": {
                    "frameTree": {
                        "frame": {
                            "id": "FRAME1",
                            "url": "https://news.ycombinator.com/",
                        }
                    }
                },
            },
            {"id": 3, "result": {"executionContextId": 42}},
            {
                "id": 4,
                "result": {
                    "result": {
                        "type": "object",
                        "value": {
                            "url": "https://news.ycombinator.com/",
                            "text": "Private page text",
                        },
                    }
                },
            },
        ]
    )
    monkeypatch.setattr(cdp_module, "_websocket_connect", lambda *_a, **_k: socket)

    result = cdp_module.fetch_tab_text(
        "ws://127.0.0.1:9222/devtools/page/T1",
        base_url="http://127.0.0.1:9222",
    )

    assert result == {"text": "Private page text", "isolated_world": True}
    assert [message["method"] for message in socket.sent] == [
        "Page.enable",
        "Page.getFrameTree",
        "Page.createIsolatedWorld",
        "Runtime.evaluate",
    ]
    assert socket.sent[3]["params"]["contextId"] == 42
    assert socket.sent[2]["params"]["grantUniversalAccess"] is False
    assert "grantUniveralAccess" not in socket.sent[2]["params"]
    assert "document.body" in socket.sent[3]["params"]["expression"]


def test_fetch_tab_text_denies_unauthorized_frame_url_before_evaluate(monkeypatch):
    from browser_fetch_router import cdp as cdp_module

    socket = _FakeWebSocket(
        [
            {"id": 1, "result": {}},
            {
                "id": 2,
                "result": {
                    "frameTree": {
                        "frame": {
                            "id": "FRAME1",
                            "url": "https://mail.google.com/mail/u/0/#inbox",
                        }
                    }
                },
            },
        ]
    )
    monkeypatch.setattr(cdp_module, "_websocket_connect", lambda *_a, **_k: socket)

    with pytest.raises(cdp_module.CdpAuthorizationError):
        cdp_module.fetch_tab_text(
            "ws://127.0.0.1:9222/devtools/page/T1",
            base_url="http://127.0.0.1:9222",
            authorize_url=lambda url: url == "https://news.ycombinator.com/",
        )

    assert [message["method"] for message in socket.sent] == [
        "Page.enable",
        "Page.getFrameTree",
    ]


def test_fetch_tab_text_denies_unauthorized_runtime_url(monkeypatch):
    from browser_fetch_router import cdp as cdp_module

    socket = _FakeWebSocket(
        [
            {"id": 1, "result": {}},
            {
                "id": 2,
                "result": {
                    "frameTree": {
                        "frame": {
                            "id": "FRAME1",
                            "url": "https://news.ycombinator.com/",
                        }
                    }
                },
            },
            {"id": 3, "result": {"executionContextId": 42}},
            {
                "id": 4,
                "result": {
                    "result": {
                        "type": "object",
                        "value": {
                            "url": "https://mail.google.com/mail/u/0/#inbox",
                            "text": "secret inbox",
                        },
                    }
                },
            },
        ]
    )
    monkeypatch.setattr(cdp_module, "_websocket_connect", lambda *_a, **_k: socket)

    with pytest.raises(cdp_module.CdpAuthorizationError):
        cdp_module.fetch_tab_text(
            "ws://127.0.0.1:9222/devtools/page/T1",
            base_url="http://127.0.0.1:9222",
            authorize_url=lambda url: url == "https://news.ycombinator.com/",
        )

    assert socket.sent[-1]["method"] == "Runtime.evaluate"


def test_websocket_connect_maps_connect_failure_to_unavailable(monkeypatch):
    from browser_fetch_router import cdp as cdp_module
    import websockets.sync.client as sync_client

    def fail_connect(*_args, **_kwargs):
        raise OSError("socket refused; cookie=secret")

    monkeypatch.setattr(sync_client, "connect", fail_connect)

    with pytest.raises(cdp_module.CdpWebSocketUnavailable) as exc:
        cdp_module._websocket_connect("ws://127.0.0.1:9222/devtools/page/T1", timeout=0.01)

    assert str(exc.value) == "cdp_websocket_connect_failed"


def test_fetch_tab_screenshot_decodes_png_from_shared_cdp_client(monkeypatch):
    from browser_fetch_router import cdp as cdp_module

    png = b"\x89PNG\r\n\x1a\nshot"
    socket = _FakeWebSocket(
        [
            {"id": 1, "result": {}},
            {
                "id": 2,
                "result": {
                    "frameTree": {
                        "frame": {
                            "id": "FRAME1",
                            "url": "https://example.com/",
                        }
                    }
                },
            },
            {"id": 3, "result": {"data": base64.b64encode(png).decode("ascii")}},
            {
                "id": 4,
                "result": {
                    "frameTree": {
                        "frame": {
                            "id": "FRAME1",
                            "url": "https://example.com/",
                        }
                    }
                },
            },
        ]
    )
    monkeypatch.setattr(cdp_module, "_websocket_connect", lambda *_a, **_k: socket)
    monkeypatch.setattr(
        cdp_module,
        "fetch_tab_list",
        lambda base_url, **_kw: [
            {
                "id": "T1",
                "title": "Private",
                "url": "https://example.com/",
                "type": "page",
                "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/T1",
            }
        ],
    )

    assert cdp_module.fetch_tab_screenshot("http://127.0.0.1:9222", "T1") == png
    assert [message["method"] for message in socket.sent] == [
        "Page.enable",
        "Page.getFrameTree",
        "Page.captureScreenshot",
        "Page.getFrameTree",
    ]


def test_fetch_tab_screenshot_denies_unauthorized_relisted_url_before_capture(monkeypatch):
    from browser_fetch_router import cdp as cdp_module

    socket = _FakeWebSocket([])
    monkeypatch.setattr(cdp_module, "_websocket_connect", lambda *_a, **_k: socket)
    monkeypatch.setattr(
        cdp_module,
        "fetch_tab_list",
        lambda base_url, **_kw: [
            {
                "id": "T1",
                "title": "Mail",
                "url": "https://mail.google.com/mail/u/0/#inbox",
                "type": "page",
                "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/T1",
            }
        ],
    )

    with pytest.raises(cdp_module.CdpAuthorizationError):
        cdp_module.fetch_tab_screenshot(
            "http://127.0.0.1:9222",
            "T1",
            authorize_url=lambda url: url == "https://news.ycombinator.com/",
        )

    assert socket.sent == []


def test_fetch_tab_screenshot_maps_relist_failure_to_unavailable(monkeypatch):
    from browser_fetch_router import cdp as cdp_module

    def fail_list(*_args, **_kwargs):
        raise RuntimeError("cdp list failed; cookie=secret")

    monkeypatch.setattr(cdp_module, "fetch_tab_list", fail_list)

    with pytest.raises(cdp_module.CdpWebSocketUnavailable) as exc:
        cdp_module.fetch_tab_screenshot("http://127.0.0.1:9222", "T1")

    assert str(exc.value) == "cdp_tab_list_failed"


def test_fetch_tab_screenshot_preserves_safety_error_from_relist(monkeypatch):
    from browser_fetch_router import cdp as cdp_module
    from browser_fetch_router.url_safety import SafetyError

    def fail_list(*_args, **_kwargs):
        raise SafetyError("blocked private address")

    monkeypatch.setattr(cdp_module, "fetch_tab_list", fail_list)

    with pytest.raises(SafetyError):
        cdp_module.fetch_tab_screenshot("http://127.0.0.1:9222", "T1")
