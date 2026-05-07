from __future__ import annotations

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


def fetch_tab_text(ws_url: str) -> dict[str, Any]:
    """Read tab text via CDP `Page.createIsolatedWorld` + `Runtime.evaluate`.

    This is a security-sensitive path. Implementation needs a WebSocket
    transport (Task 14 will add the runtime dep — declared in
    pyproject.toml in the same PR that wires the import, per the
    "no unused declared deps" rule) plus careful framing of
    `document.body.innerText`-equivalent extraction inside an
    isolated world so page JS cannot intercept the read.
    """
    raise NotImplementedError("isolated_world_extraction_pending")


def fetch_tab_screenshot(base_url: str, target: str) -> bytes:
    """Capture screenshot via `Page.captureScreenshot`. Live wiring deferred —
    same gating reason as fetch_tab_text."""
    raise NotImplementedError("cdp_screenshot_pending")
