from __future__ import annotations

import base64
import json
import os
from typing import Any
from urllib.parse import urlsplit

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost", "[::1]"}

# Cap CDP /json responses. Chrome's tab list normally fits in a few KB; a 1 MB
# ceiling stops a malicious or runaway endpoint from exhausting memory when
# the user passes --allow-remote-cdp.
MAX_CDP_RESPONSE_BYTES = 1_048_576


class CdpResponseTooLarge(RuntimeError):
    pass


class CdpUnexpectedRedirect(RuntimeError):
    """A CDP server responded with a 3xx — a real Chrome DevTools target
    never redirects /json. Distinct from a transport-level UnsafeUrl so
    callers can surface a CDP-specific tool_setup_failed code without
    classifying the situation as an SSRF block."""


class CdpWebSocketUrlInvalid(RuntimeError):
    """The tab did not expose a usable DevTools WebSocket URL."""


class CdpWebSocketUrlMismatch(RuntimeError):
    """The tab WebSocket URL does not belong to the validated CDP base."""


class CdpWebSocketUnavailable(RuntimeError):
    """The DevTools WebSocket could not be opened or read."""


class CdpWebSocketDependencyMissing(RuntimeError):
    """The declared WebSocket runtime dependency is not importable."""


class CdpProtocolError(RuntimeError):
    """The DevTools protocol response was missing, malformed, or failed."""


def cdp_base_url(*, allow_remote: bool = False) -> str | None:
    """Resolve the Chrome DevTools Protocol HTTP endpoint.

    First-pass URL-level validation; the actual fetch in
    `fetch_tab_list` runs through `SafeHttpClient(loopback_ok=True,
    follow_redirects=False)` which re-validates and DNS-pins on the
    second resolution. The two layers compose into defense in depth.

    Loopback hosts (127.0.0.1, ::1, localhost) are accepted
    unconditionally — that's the default intended path and the user's
    own browser.

    Non-loopback hosts require `--allow-remote-cdp` AND must pass the
    same SSRF policy SafeHttpClient enforces: blocked-IP literals
    (link-local, metadata endpoints, private RFC 1918, loopback
    aliases, etc.), blocked hostnames (metadata.google.internal etc.),
    AND every DNS-resolved answer must be public.

    Without this validation `BFR_CDP_URL=http://169.254.169.254:80`
    plus `--allow-remote-cdp` would have let the downstream HTTP call
    reach the AWS IMDS endpoint — a real SSRF bypass since
    `BFR_CDP_URL` propagates to spawned subprocesses via
    `SAFE_BASE_ENV_KEYS` (Greptile P1 on commit c4e3d93).

    The original round-5b fix here had a residual DNS-rebinding TOCTOU
    between this validation and the second resolution in
    `urllib.request.urlopen`. Round-6 r6-01 closed that class by
    routing `fetch_tab_list` through `SafeHttpClient` (DNS-pinned;
    redirect-rejected). This function now serves as the explicit
    `--allow-remote-cdp` gate plus a fast-fail front door; the
    transport is the authoritative SSRF boundary.
    """
    value = os.environ.get("BFR_CDP_URL", "http://127.0.0.1:9222")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"}:
        return None
    if parsed.username or parsed.password:
        # Embedded credentials in CDP URL — never legitimate, rejected
        # at the same layer as normalize_and_validate_url.
        return None
    host = (parsed.hostname or "").lower()
    if not host:
        return None
    # The env var contract is `scheme://host[:port]` — a bare CDP base
    # URL. Anything richer (path, query, fragment) is rejected because
    # `fetch_tab_list` appends `/json` after `rstrip('/')`, and a
    # fragment in BFR_CDP_URL would silently consume the appended
    # `/json` (urlsplit treats `#frag/json` as fragment="frag/json",
    # path=""), routing the wire request to `/` instead. A bare
    # trailing slash (path == "/") is preserved as the historical
    # accepted form (round-11 i04).
    if (parsed.path and parsed.path != "/") or parsed.query or parsed.fragment:
        return None
    if host in LOOPBACK_HOSTS:
        return value.rstrip("/")
    if not allow_remote:
        return None
    # Non-loopback path: enforce the SSRF policy.
    import ipaddress
    import socket

    from browser_fetch_router.url_safety import (
        BLOCKED_HOSTS,
        UnsafeUrl,
        _parse_ip,
        is_blocked_ip,
    )

    if host in BLOCKED_HOSTS:
        return None
    try:
        ip_literal = _parse_ip(host)
    except UnsafeUrl:
        # `_parse_ip` raises for obfuscated forms (octal, hex,
        # leading-zero dotted) — treat any such case as blocked.
        return None
    if ip_literal is not None:
        if is_blocked_ip(ip_literal):
            return None
        return value.rstrip("/")
    # Hostname (not a literal): resolve DNS and validate every answer.
    # A CNAME or A-record that resolves to a private IP must be rejected
    # — same rule SafeHttpClient applies.
    try:
        # `AI_ADDRCONFIG` mirrors the resolver in `http_client._default_resolver`
        # — only families actually configured on the box are returned, so a
        # v6-only host on a v4-only machine doesn't yield a useless AAAA
        # answer that the connector would later fail on. (Gemini medium
        # on commit 3b131b7: consistency with the SafeHttpClient resolver.)
        infos = socket.getaddrinfo(
            host, None, type=socket.SOCK_STREAM, flags=socket.AI_ADDRCONFIG
        )
    except OSError:
        return None
    if not infos:
        return None
    for family, _socktype, _proto, _canonname, sockaddr in infos:
        try:
            ip_obj = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            return None
        if is_blocked_ip(ip_obj):
            return None
    return value.rstrip("/")


def _default_port(scheme: str) -> int | None:
    if scheme in {"http", "ws"}:
        return 80
    if scheme in {"https", "wss"}:
        return 443
    return None


def _effective_port(parsed) -> int | None:
    return parsed.port if parsed.port is not None else _default_port(parsed.scheme)


def validate_tab_websocket_url(ws_url: str, base_url: str) -> str:
    """Validate a tab-level CDP WebSocket URL against the CDP HTTP base."""
    if not isinstance(ws_url, str) or not ws_url.strip():
        raise CdpWebSocketUrlInvalid("missing_tab_websocket_url")
    candidate = ws_url.strip()
    ws = urlsplit(candidate)
    if ws.scheme not in {"ws", "wss"}:
        raise CdpWebSocketUrlInvalid("invalid_tab_websocket_scheme")
    if ws.username or ws.password:
        raise CdpWebSocketUrlInvalid("embedded_credentials_not_allowed")
    if not ws.hostname or not ws.path:
        raise CdpWebSocketUrlInvalid("malformed_tab_websocket_url")
    if ws.query or ws.fragment:
        raise CdpWebSocketUrlInvalid("tab_websocket_url_must_not_include_query_or_fragment")

    base = urlsplit(base_url)
    if base.scheme not in {"http", "https"} or not base.hostname:
        raise CdpWebSocketUrlMismatch("invalid_cdp_base_url")
    expected_ws_scheme = "wss" if base.scheme == "https" else "ws"
    if ws.scheme != expected_ws_scheme:
        raise CdpWebSocketUrlMismatch("tab_websocket_scheme_mismatch")
    if ws.hostname.lower() != base.hostname.lower():
        raise CdpWebSocketUrlMismatch("tab_websocket_host_mismatch")
    if _effective_port(ws) != _effective_port(base):
        raise CdpWebSocketUrlMismatch("tab_websocket_port_mismatch")
    return candidate


def _websocket_connect(ws_url: str, *, timeout: float):
    try:
        from websockets.sync.client import connect
    except Exception as exc:  # pragma: no cover - exercised by package install checks
        raise CdpWebSocketDependencyMissing("websockets dependency is not installed") from exc
    try:
        return connect(ws_url, open_timeout=timeout, close_timeout=timeout)
    except TypeError:
        # Older sync-client releases accepted fewer timeout keyword arguments.
        try:
            return connect(ws_url, open_timeout=timeout)
        except Exception as exc:
            raise CdpWebSocketUnavailable("cdp_websocket_connect_failed") from exc
    except Exception as exc:
        raise CdpWebSocketUnavailable("cdp_websocket_connect_failed") from exc


def _send_cdp_command(
    websocket: Any,
    command_id: int,
    method: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"id": command_id, "method": method}
    if params is not None:
        payload["params"] = params
    try:
        websocket.send(json.dumps(payload, separators=(",", ":")))
    except Exception as exc:
        raise CdpWebSocketUnavailable("cdp_websocket_write_failed") from exc
    for _ in range(100):
        try:
            raw = websocket.recv()
        except Exception as exc:
            raise CdpWebSocketUnavailable("cdp_websocket_read_failed") from exc
        try:
            message = json.loads(raw)
        except Exception as exc:
            raise CdpProtocolError(f"{method} returned invalid JSON") from exc
        if not isinstance(message, dict) or message.get("id") != command_id:
            continue
        if "error" in message or "exceptionDetails" in message:
            raise CdpProtocolError(f"{method} failed")
        result = message.get("result")
        if not isinstance(result, dict):
            raise CdpProtocolError(f"{method} returned malformed result")
        return result
    raise CdpProtocolError(f"{method} response not received")


def fetch_tab_list(base_url: str, *, timeout: float = 3.0) -> list[dict[str, Any]]:
    """GET <base>/json — DevTools `Browser.GetTargets`-equivalent endpoint.

    Routed through `SafeHttpClient` so the CDP transport gets the same
    DNS-pinning + redirect rejection that read-web's transport already
    enjoys. The previous implementation called `urllib.request.urlopen`
    directly; that opener installs `HTTPRedirectHandler` by default, so
    a compromised loopback CDP server returning `302 Location: http://
    169.254.169.254/...` would be transparently followed and the IMDS
    body parsed as a tab list (round-6 r6-01 CRITICAL). Two flags close
    the bypass:

    - `loopback_ok=is_loopback_host` permits the user's own browser on
      127.0.0.0/8 / ::1 / `localhost` while keeping every other blocked
      category (link-local, RFC 1918 private, IMDS literals,
      metadata.google.internal, etc.) rejected.
    - `follow_redirects=False` raises `UnsafeUrl("unexpected_redirect:
      <code>")` on any 3xx — surfaced here as `CdpUnexpectedRedirect`
      so callers can emit a CDP-specific `tool_setup_failed` envelope.

    Pinning DNS once inside SafeHttpClient also closes the residual
    rebinding TOCTOU acknowledged in the round-5b commit on `cdp_base_url`
    — there is no second resolution.

    Reads at most MAX_CDP_RESPONSE_BYTES; above that SafeHttpClient
    raises `ResponseTooLarge` which is converted to `CdpResponseTooLarge`
    for the existing call-site contract.
    """
    from browser_fetch_router.http_client import (
        ResponseTooLarge,
        SafeHttpClient,
    )
    from browser_fetch_router.url_safety import UnsafeUrl

    # `loopback_ok=True` is the CDP transport regime: it permits 127.0.0.0/8
    # / ::1 / `localhost` AND non-default ports (CDP runs on 9222). For
    # the remote-CDP path the IP was already validated by `cdp_base_url`
    # (which rejects blocked-IP literals, BLOCKED_HOSTS, and per-answer
    # DNS resolution) before the URL gets here, so SafeHttpClient
    # relaxing the loopback IP rule on the second resolution is fine —
    # a DNS rebind from the validated public IP back to loopback would
    # land on the user's own browser, which is the intended CDP endpoint
    # anyway. Other blocked classes (link-local, RFC 1918 private,
    # IMDS literals, metadata.google.internal) stay rejected in both
    # modes.
    client = SafeHttpClient(timeout=timeout, loopback_ok=True)
    url = base_url.rstrip("/") + "/json"
    try:
        response = client.get_text(
            url,
            max_bytes=MAX_CDP_RESPONSE_BYTES,
            follow_redirects=False,
            extra_headers={"Accept": "application/json"},
        )
    except ResponseTooLarge as exc:
        raise CdpResponseTooLarge(
            f"cdp_response_exceeded_{MAX_CDP_RESPONSE_BYTES}_bytes"
        ) from exc
    except UnsafeUrl as exc:
        if str(exc).startswith("unexpected_redirect"):
            raise CdpUnexpectedRedirect(str(exc)) from exc
        raise
    raw = json.loads(response.text)
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "id": item.get("id"),
                "title": item.get("title"),
                "url": item.get("url"),
                "type": item.get("type"),
                "webSocketDebuggerUrl": item.get("webSocketDebuggerUrl"),
            }
        )
    return out


def fetch_tab_text(
    ws_url: str,
    *,
    base_url: str | None = None,
    timeout: float = 3.0,
) -> dict[str, Any]:
    """Read tab text via CDP `Page.createIsolatedWorld` + `Runtime.evaluate`.

    The optional `base_url` lets callers enforce that the tab-level
    WebSocket URL came from the same already-validated CDP endpoint used
    to list tabs.
    """
    if base_url is not None:
        ws_url = validate_tab_websocket_url(ws_url, base_url)
    with _websocket_connect(ws_url, timeout=timeout) as websocket:
        _send_cdp_command(websocket, 1, "Page.enable")
        frame_tree = _send_cdp_command(websocket, 2, "Page.getFrameTree")
        try:
            frame_id = frame_tree["frameTree"]["frame"]["id"]
        except Exception as exc:
            raise CdpProtocolError("Page.getFrameTree returned no main frame") from exc
        world = _send_cdp_command(
            websocket,
            3,
            "Page.createIsolatedWorld",
            {
                "frameId": frame_id,
                "worldName": "browser-fetch-router",
                "grantUniversalAccess": False,
            },
        )
        context_id = world.get("executionContextId")
        if not isinstance(context_id, int):
            raise CdpProtocolError("Page.createIsolatedWorld returned no context")
        evaluated = _send_cdp_command(
            websocket,
            4,
            "Runtime.evaluate",
            {
                "contextId": context_id,
                "returnByValue": True,
                "awaitPromise": False,
                "expression": (
                    "(() => { const body = document.body; "
                    "if (!body) return ''; "
                    "return body.innerText || body.textContent || ''; })()"
                ),
            },
        )
    result = evaluated.get("result")
    if not isinstance(result, dict):
        raise CdpProtocolError("Runtime.evaluate returned malformed result")
    value = result.get("value", "")
    if value is None:
        value = ""
    if not isinstance(value, str):
        value = str(value)
    return {"text": value, "isolated_world": True}


def fetch_tab_screenshot(
    base_url: str,
    target: str,
    *,
    timeout: float = 3.0,
) -> bytes:
    """Capture screenshot via `Page.captureScreenshot` over the shared CDP path."""
    try:
        tabs = fetch_tab_list(base_url, timeout=timeout)
    except Exception as exc:
        raise CdpWebSocketUnavailable("cdp_tab_list_failed") from exc
    page_tabs = [tab for tab in tabs if tab.get("type") == "page"]
    tab = None
    if target == "active":
        tab = page_tabs[0] if page_tabs else None
    if tab is None:
        for candidate in tabs:
            if candidate.get("id") == target or candidate.get("url") == target:
                tab = candidate
                break
    if tab is None:
        raise CdpProtocolError("target tab not found")
    ws_url = validate_tab_websocket_url(tab.get("webSocketDebuggerUrl") or "", base_url)
    with _websocket_connect(ws_url, timeout=timeout) as websocket:
        _send_cdp_command(websocket, 1, "Page.enable")
        captured = _send_cdp_command(
            websocket,
            2,
            "Page.captureScreenshot",
            {"format": "png", "fromSurface": True},
        )
    data = captured.get("data")
    if not isinstance(data, str) or not data:
        raise CdpProtocolError("Page.captureScreenshot returned no data")
    try:
        return base64.b64decode(data, validate=True)
    except Exception as exc:
        raise CdpProtocolError("Page.captureScreenshot returned invalid data") from exc
