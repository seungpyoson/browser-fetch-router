from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from browser_fetch_router.approvals import (
    add_approval,
    can_read_url,
    list_active_scopes,
    normalize_scope,
    revoke_scope,
)
from browser_fetch_router.cdp import cdp_base_url, fetch_tab_list
from browser_fetch_router.cdp import (
    CdpAuthorizationError,
    CdpProtocolError,
    CdpResponseTooLarge,
    CdpTabListMalformedJson,
    CdpTabMissingId,
    CdpUnexpectedRedirect,
    CdpWebSocketDependencyMissing,
    CdpWebSocketUnavailable,
    CdpWebSocketUrlInvalid,
    CdpWebSocketUrlMismatch,
    validate_tab_websocket_url,
)
from browser_fetch_router.default_deny import is_default_denied
from browser_fetch_router.paths import (
    UnsafeDestination,
    atomic_write_bytes,
    validate_image_dest,
)
from browser_fetch_router.schema import envelope
from browser_fetch_router.url_safety import SafetyError


_NON_HTTP_SCHEMES_TO_REDACT = frozenset({
    # Documentation of the schemes the round-3 fix originally enumerated.
    # No longer used at runtime — `unknown_scheme` (any scheme outside
    # {http, https}) supersedes the explicit list because the safe-by-
    # default-deny check rejects all non-http(s) schemes anyway. Kept
    # here as a comment for future maintainers reviewing scheme handling
    # so they know which schemes were specifically considered.
    "javascript", "data", "file", "chrome-extension", "moz-extension",
    "about", "blob", "view-source", "ftp", "ws", "wss",
})


@dataclass(frozen=True)
class _ReadAuthorization:
    persistent_scopes: tuple[str, ...]
    exact_one_time_scopes: tuple[str, ...]


def _authorization_for_request(
    *,
    approval_scope: str | None,
    session_id: str | None,
) -> _ReadAuthorization:
    persistent = list_active_scopes(session_id=session_id or "")
    exact_one_time: list[str] = []
    if approval_scope:
        norm = normalize_scope(approval_scope)
        if norm.startswith("exact:"):
            exact_one_time.append(norm)
        else:
            # Hostname/wildcard approvals must be explicitly persisted to
            # apply to subsequent calls; for this single call they count too.
            persistent.append(norm)
    return _ReadAuthorization(
        persistent_scopes=tuple(persistent),
        exact_one_time_scopes=tuple(exact_one_time),
    )


def _is_url_authorized(url: str, auth: _ReadAuthorization) -> bool:
    return can_read_url(
        url,
        list(auth.persistent_scopes),
        exact_one_time=list(auth.exact_one_time_scopes),
    )


def _current_url_authorizer(auth: _ReadAuthorization):
    def authorize(current_url: str) -> bool:
        return _is_url_authorized(current_url, auth)

    return authorize


def _current_url_denial_envelope(
    *,
    url: str | None,
    approval_scope: str | None,
    tab_id: Any,
) -> dict[str, Any]:
    return envelope(
        command="read-user-tabs",
        status="approval_required",
        url=url,
        approval={"required": True, "scope": approval_scope},
        error={"code": "approval_required_for_current_tab"},
        evidence={"tab_id": tab_id},
    )


def _tab_list_failure_envelope(
    exc: BaseException,
    *,
    cdp_base: str | None = None,
) -> dict[str, Any]:
    classified = _classified_cdp_error(exc)
    code, message = classified or (
        "cdp_unreachable",
        "CDP tab list endpoint was unreachable.",
    )
    evidence = {"cdp_base": cdp_base} if cdp_base is not None else None
    return envelope(
        command="read-user-tabs",
        status="tool_setup_failed",
        error={"code": code, "message": message},
        evidence=evidence,
    )


def _classified_cdp_error(exc: BaseException) -> tuple[str, str] | None:
    if isinstance(exc, CdpUnexpectedRedirect):
        return "cdp_unexpected_redirect", "CDP tab list endpoint returned an unexpected redirect."
    if isinstance(exc, CdpTabListMalformedJson):
        return "cdp_tab_list_malformed_json", "CDP tab list endpoint returned malformed JSON."
    if isinstance(exc, CdpResponseTooLarge):
        return "cdp_response_too_large", "CDP tab list response exceeded the configured size limit."
    if isinstance(exc, CdpTabMissingId):
        return "cdp_tab_missing_id", "Resolved tab did not expose a CDP tab id."
    return None


def _cdp_failure_envelope(
    exc: BaseException,
    *,
    url: str | None,
    operation: str,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    classified = _classified_cdp_error(exc)
    if classified is not None:
        code, message = classified
    elif isinstance(exc, CdpWebSocketUrlInvalid):
        code = "cdp_websocket_url_invalid"
        message = "Tab did not expose a valid CDP WebSocket URL."
    elif isinstance(exc, CdpWebSocketUrlMismatch):
        code = "cdp_websocket_url_mismatch"
        message = "Tab CDP WebSocket URL did not match the validated CDP endpoint."
    elif isinstance(exc, CdpWebSocketDependencyMissing):
        code = "cdp_websocket_dependency_missing"
        message = "websockets dependency is not installed."
    elif isinstance(exc, CdpWebSocketUnavailable):
        code = "cdp_unreachable"
        message = "CDP WebSocket endpoint was unreachable."
    elif isinstance(exc, CdpProtocolError):
        code = "cdp_screenshot_failed" if operation == "screenshot" else "cdp_text_extraction_failed"
        message = "CDP protocol command failed."
    else:
        code = "cdp_screenshot_failed" if operation == "screenshot" else "cdp_text_extraction_failed"
        message = "CDP operation failed."
    return envelope(
        command="read-user-tabs",
        status="tool_setup_failed",
        url=url,
        error={"code": code, "message": message},
        evidence=evidence,
    )


def redact_tab_list(tabs: list[dict[str, Any]], *, show_all: bool = False) -> list[dict[str, Any]]:
    """Redact default-deny tabs and non-HTTP-scheme tabs UNCONDITIONALLY.

    Default-deny redaction is ALWAYS applied regardless of `show_all` —
    a display flag must never grant authorization (Greptile #2 on commit
    7ffd4c8: an agent calling `bfr read-user-tabs list --show-all`
    previously revealed the active tab's full URL even when it was on
    the default-deny list, with no stored approval required).

    The `show_all` parameter is preserved as a no-op for the default-
    deny branch, kept for API compatibility. To see a default-denied
    tab's URL, use `read-tab` with an explicit per-URL approval scope —
    that path goes through `can_read_url` which gates `exact:` one-time
    approvals against the deny list.

    Two redaction triggers, both unconditional:
    - URL is on the default-deny list (mail.google.com, etc.).
    - URL scheme is non-HTTP (javascript:, data:, file:, chrome-extension:,
      etc.). These never match the default-deny patterns because
      `urlsplit` returns no hostname for them, but they DO leak content
      (a `data:` URL can embed an entire HTML page; `javascript:` URLs
      leak code; `file://` paths leak local filesystem layout).
    """
    # `show_all` is intentionally accepted but unused — explicit
    # signal that the parameter survives for CLI compatibility while
    # the auth model now lives elsewhere (per-URL approval scopes).
    _ = show_all
    out: list[dict[str, Any]] = []
    for tab in tabs:
        url = tab.get("url") or ""
        # Use urllib.parse.urlsplit for scheme extraction so URLs with
        # colons in the path or non-URL strings (titles, free text) are
        # parsed correctly rather than misclassified by a naive split
        # (Gemini #3 on commit 7ffd4c8). For HTTP(S) URLs `urlsplit`
        # returns the canonical scheme; for opaque URIs like `data:` or
        # `javascript:` it still extracts the scheme correctly; for
        # non-URL text it returns an empty scheme (so the entry is
        # treated as having no scheme and is NOT classified non-HTTP —
        # but also is NOT a recognized HTTP URL, so default-deny still
        # gets a chance to redact it).
        scheme = urlsplit(url).scheme.lower()
        # Redact unconditionally if the URL is on the default-deny list,
        # OR if the scheme is anything other than http/https. The single
        # `unknown_scheme` check covers the explicit list previously
        # tracked in `_NON_HTTP_SCHEMES_TO_REDACT` AND any future schemes
        # (chrome://, ipfs://, etc.) — defense-in-depth via allow-list
        # rather than enumerate-and-block (Gemini medium on 9a26fb2).
        unknown_scheme = bool(scheme) and scheme not in {"http", "https"}
        if is_default_denied(url) or unknown_scheme:
            out.append({
                "id": tab.get("id"),
                "title": "[hidden]",
                "url": "[hidden]",
                "redacted": True,
            })
        else:
            out.append({
                "id": tab.get("id"),
                "title": tab.get("title"),
                "url": url,
                "redacted": False,
            })
    return out


def cap_content(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n\n[TRUNCATED after {max_chars} chars]"


def list_tabs(
    *,
    all_tabs: bool = False,
    show_all: bool = False,
    allow_remote_cdp: bool = False,
    session_id: str | None = None,
    approval_scope: str | None = None,
    persist_approval: bool = False,
) -> dict[str, Any]:
    """List browser tabs.

    `all_tabs` and `show_all` are SEPARATE concerns and previously got
    conflated in a way that turned `--show-all` into an authorization
    bypass for `--all`:

    - `all_tabs=True` lists EVERY tab the browser knows about. This leaks
      browsing history and requires the `exact:list-all-tabs` approval
      scope, FULL STOP. The previous code allowed `--show-all` to bypass
      the approval check, which had nothing to do with display semantics.
    - `show_all=True` controls REDACTION of default-deny tabs in the
      output ONLY. It does not grant any new authority. A caller can pass
      `--show-all` to see hostnames of password-manager tabs they were
      already authorized to know about; without it, those tabs are
      redacted.
    - `approval_scope` / `persist_approval` enable single-command
      authorization parity with `read_tab` / `screenshot_tab`. Caller
      passes `--approval-scope=exact:list-all-tabs --persist-approval
      --all` to grant the broad-listing scope and execute the listing
      in one invocation, without a separate `add-approval` round trip
      (Gemini medium on commit 3b131b7). Only the literal sentinel
      `exact:list-all-tabs` is meaningful here; any other scope is
      stored but does not unlock list-all.
    """
    base = cdp_base_url(allow_remote=allow_remote_cdp)
    if base is None:
        return envelope(
            command="read-user-tabs",
            status="tool_setup_failed",
            error={"code": "remote_cdp_not_allowed", "message": "CDP endpoint is non-loopback; pass --allow-remote-cdp to opt in"},
        )
    if all_tabs:
        # Approval check is unconditional — `show_all` must NOT bypass it.
        # Self-approval via --approval-scope happens BEFORE the active-
        # scopes check so a single CLI invocation can both grant and
        # exercise the list-all-tabs permission.
        if approval_scope:
            add_approval(
                approval_scope,
                session_id=session_id or "",
                persisted=bool(persist_approval),
            )
        scopes = list_active_scopes(session_id=session_id or "")
        if "exact:list-all-tabs" not in scopes:
            return envelope(
                command="read-user-tabs",
                status="approval_required",
                approval={"required": True, "scope": "exact:list-all-tabs"},
                error={"code": "list_all_requires_approval", "message": "Listing every tab leaks browsing history. Approve with --approval-scope=exact:list-all-tabs and --persist-approval"},
            )
    try:
        tabs = fetch_tab_list(base)
    except SafetyError:
        # SSRF / DNS rebinding / host-header smuggling surfaced from the
        # CDP layer MUST propagate so the dispatcher emits
        # unsafe_url_blocked (exit 4) — not get reclassified as a setup
        # failure (exit 3).
        raise
    except Exception as exc:
        return _tab_list_failure_envelope(exc, cdp_base=base)
    if not all_tabs:
        # Default: only the most recent (active) page-type tab.
        page_tabs = [t for t in tabs if t.get("type") == "page"]
        tabs = page_tabs[:1]
    redacted = redact_tab_list(tabs, show_all=show_all)
    return envelope(
        command="read-user-tabs",
        status="ok",
        evidence={"cdp_base": base, "tab_count": len(redacted), "tabs": redacted},
    )


def _resolve_and_authorize_tab(
    target: str,
    *,
    approval_scope: str | None,
    persist_approval: bool,
    allow_remote_cdp: bool,
    session_id: str | None,
) -> tuple[str | None, str | None, dict[str, Any] | None, _ReadAuthorization | None, dict[str, Any] | None]:
    """Resolve `target` to a tab and authorize the URL.

    Returns `(base, url, tab_dict, auth_context, None)` on success, or
    `(None, None, None, None, error_envelope)` on any failure (CDP unreachable,
    tab not found, approval required). Used by BOTH `read_tab` AND
    `screenshot_tab` so the approval check cannot be skipped on one path
    — previously `screenshot_tab` went straight to fetch_tab_screenshot
    without any can_read_url gate, exposing protected page contents as
    PNG without authorization.
    """
    base = cdp_base_url(allow_remote=allow_remote_cdp)
    if base is None:
        return None, None, None, None, envelope(
            command="read-user-tabs",
            status="tool_setup_failed",
            error={"code": "remote_cdp_not_allowed"},
        )
    try:
        tabs = fetch_tab_list(base)
    except SafetyError:
        # SSRF / DNS rebinding from CDP layer must propagate.
        raise
    except Exception as exc:
        return None, None, None, None, _tab_list_failure_envelope(exc, cdp_base=base)
    tab = _resolve_tab(target, tabs)
    if tab is None:
        return None, None, None, None, envelope(
            command="read-user-tabs",
            status="tool_setup_failed",
            error={"code": "tab_not_found", "message": f"No tab matched {target!r}"},
        )
    url = tab.get("url") or ""
    auth = _authorization_for_request(
        approval_scope=approval_scope,
        session_id=session_id,
    )
    if not _is_url_authorized(url, auth):
        return None, None, None, None, envelope(
            command="read-user-tabs",
            status="approval_required",
            url=url,
            approval={"required": True, "scope": approval_scope},
            error={"code": "approval_required_for_tab"},
        )
    # Record the approval (persisted or session-only) ONLY when the URL is
    # not default-denied. Without the unified guard, the non-persist branch
    # would write a ghost session-scoped record for a default-denied URL
    # (e.g. `exact:https://mail.google.com/...`) — `can_read_url` still
    # blocks reads via its own `is_default_denied` check, so no access is
    # granted, but the store ends up polluted with records that can never
    # match. Same guard now governs both branches.
    if approval_scope and not is_default_denied(url):
        add_approval(
            approval_scope,
            session_id=session_id or "",
            persisted=bool(persist_approval),
        )
    return base, url, tab, auth, None


def read_tab(
    target: str,
    *,
    approval_scope: str | None = None,
    persist_approval: bool = False,
    allow_remote_cdp: bool = False,
    max_chars: int = 20_000,
    session_id: str | None = None,
) -> dict[str, Any]:
    base, url, tab, auth, error = _resolve_and_authorize_tab(
        target,
        approval_scope=approval_scope,
        persist_approval=persist_approval,
        allow_remote_cdp=allow_remote_cdp,
        session_id=session_id,
    )
    if error is not None:
        return error
    # CDP text extraction is wired in cdp.fetch_tab_text; if unavailable we
    # report tool_setup_failed rather than reading the page-level world.
    # `fetch_tab_text(base_url=None)` is intentional here: this boundary
    # validates the tab WebSocket URL before invoking the CDP helper.
    ws_url = tab.get("webSocketDebuggerUrl") or ""
    try:
        ws_url = validate_tab_websocket_url(ws_url, base)
    except (CdpWebSocketUrlInvalid, CdpWebSocketUrlMismatch) as exc:
        return _cdp_failure_envelope(
            exc,
            url=url,
            operation="text",
            evidence={"tab_id": tab.get("id")},
        )
    try:
        from browser_fetch_router.cdp import fetch_tab_text  # local import to avoid websocket dep at import time
        result = fetch_tab_text(
            ws_url,
            base_url=None,
            authorize_url=_current_url_authorizer(auth),
        )
    except CdpAuthorizationError:
        return _current_url_denial_envelope(
            url=url,
            approval_scope=approval_scope,
            tab_id=tab.get("id"),
        )
    except SafetyError:
        raise
    except (CdpWebSocketUrlInvalid, CdpWebSocketUrlMismatch, CdpWebSocketDependencyMissing, CdpWebSocketUnavailable, CdpProtocolError) as exc:
        return _cdp_failure_envelope(
            exc,
            url=url,
            operation="text",
            evidence={"tab_id": tab.get("id")},
        )
    except Exception as exc:
        return _cdp_failure_envelope(
            exc,
            url=url,
            operation="text",
            evidence={"tab_id": tab.get("id")},
        )
    text = cap_content(result.get("text", ""), max_chars)
    return envelope(
        command="read-user-tabs",
        status="ok",
        url=url,
        title=tab.get("title"),
        content_markdown=text,
        evidence={
            "cdp_isolated_world": result.get("isolated_world", False),
            "tab_id": tab.get("id"),
        },
    )


def screenshot_tab(
    target: str,
    *,
    output: Path | str,
    approval_scope: str | None = None,
    persist_approval: bool = False,
    allow_remote_cdp: bool = False,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Capture a screenshot of a browser tab.

    A screenshot of a protected page (banking, mail, password vault) is
    EVERY BIT as sensitive as reading its DOM — a PNG of an inbox is
    trivially OCR'd. Previous code went straight from `target` to
    `fetch_tab_screenshot` without ANY can_read_url check, leaving an
    auth bypass that the round-3 `test_G_screenshot_tab_skips_approval_check`
    repro confirmed. The fix routes screenshot through the same
    `_resolve_and_authorize_tab` helper as `read_tab` so the auth gate
    is applied identically.
    """
    # Class-D round-17: validate the agent-supplied output path before
    # any side effect. See W2/W3/W6 in cli-write-containment-contract.md
    # and the `validate_image_dest` docstring for the invariant. PNG
    # bytes overwriting `~/.ssh/authorized_keys` corrupts SSH access
    # (DoS); PNG bytes overwriting `~/.bashrc` corrupts shell startup
    # (DoS) — both are caught at the validator boundary.
    try:
        output = validate_image_dest(output)
    except UnsafeDestination as exc:
        return envelope(
            command="read-user-tabs",
            status="usage_error",
            error={"code": "unsafe_output_destination", "message": str(exc)},
        )
    if not output.parent.exists() or not output.parent.is_dir():
        return envelope(
            command="read-user-tabs",
            status="usage_error",
            error={"code": "output_parent_missing", "message": f"{output.parent} must exist"},
        )
    base, url, tab, auth, error = _resolve_and_authorize_tab(
        target,
        approval_scope=approval_scope,
        persist_approval=persist_approval,
        allow_remote_cdp=allow_remote_cdp,
        session_id=session_id,
    )
    if error is not None:
        return error
    tab_id = tab.get("id")
    if not isinstance(tab_id, str) or not tab_id:
        return _cdp_failure_envelope(
            CdpTabMissingId("target tab did not expose a CDP tab id"),
            url=url,
            operation="screenshot",
            evidence={"tab_id": tab_id},
        )
    ws_url = tab.get("webSocketDebuggerUrl") or ""
    try:
        # This first-list check fails fast for missing or mismatched tab
        # metadata. `fetch_tab_screenshot` deliberately re-lists and
        # re-validates before capture so a tab-list race cannot reuse stale
        # WebSocket metadata.
        validate_tab_websocket_url(ws_url, base)
    except (CdpWebSocketUrlInvalid, CdpWebSocketUrlMismatch) as exc:
        return _cdp_failure_envelope(
            exc,
            url=url,
            operation="screenshot",
            evidence={"tab_id": tab_id},
        )
    try:
        from browser_fetch_router.cdp import fetch_tab_screenshot
        png_bytes = fetch_tab_screenshot(
            base,
            tab_id,
            authorize_url=_current_url_authorizer(auth),
        )
    except CdpAuthorizationError:
        return _current_url_denial_envelope(
            url=url,
            approval_scope=approval_scope,
            tab_id=tab_id,
        )
    except SafetyError:
        raise
    except (CdpWebSocketUrlInvalid, CdpWebSocketUrlMismatch, CdpWebSocketDependencyMissing, CdpWebSocketUnavailable, CdpProtocolError) as exc:
        return _cdp_failure_envelope(
            exc,
            url=url,
            operation="screenshot",
            evidence={"tab_id": tab_id},
        )
    except Exception as exc:
        return _cdp_failure_envelope(
            exc,
            url=url,
            operation="screenshot",
            evidence={"tab_id": tab_id},
        )
    # Route through atomic_write_bytes (mode=0o600 set on the temp file
    # before os.replace) so the visible target file appears at 0o600
    # from the moment it exists. The previous `write_bytes(...)` +
    # `os.chmod(...)` left a TOCTOU window where the file was visible
    # at the umask-default mode (typically 0o644) before the chmod
    # tightened it — a sibling local-user process could read the
    # sensitive screenshot during that window. Class fix r15-03 (the
    # screenshot is "every bit as sensitive as reading its DOM" per
    # this function's own docstring; OCR'd inbox PNGs leak credentials
    # equivalently to leaked HTML).
    atomic_write_bytes(output, png_bytes, mode=0o600)
    return envelope(
        command="read-user-tabs",
        status="ok",
        url=url,
        artifacts=[{"path": str(output), "kind": "image/png"}],
    )


def revoke(scope: str, *, session_id: str | None = None) -> dict[str, Any]:
    result = revoke_scope(scope)
    return envelope(
        command="read-user-tabs",
        status="ok",
        evidence=result,
    )


# --------- helpers ----------------------------------------------------------


def _resolve_tab(target: str, tabs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Match a CLI tab target to a tab dict.

    Accepts ONLY:
    - "active" → first page-type tab
    - exact tab id
    - exact URL match

    Title-substring matching was removed (2026-05-06 internal review): a
    substring like "x" matches every tab containing that letter and would
    silently route to the wrong tab. Callers MUST use one of the exact
    forms above.
    """
    page_tabs = [t for t in tabs if t.get("type") == "page"]
    if target == "active":
        return page_tabs[0] if page_tabs else None
    for t in tabs:
        if t.get("id") == target:
            return t
    for t in tabs:
        if t.get("url") == target:
            return t
    return None
