from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from browser_fetch_router.http_client import SafeHttpClient
from browser_fetch_router.url_safety import SafetyError


def reader_url(x_url: str) -> str:
    """Build the FxTwitter API URL for a given X/Twitter status URL."""
    parsed = urlsplit(x_url)
    return urlunsplit(("https", "api.fxtwitter.com", parsed.path, parsed.query, ""))


def fetch(url: str, ctx: dict[str, Any]) -> dict[str, Any]:
    """Fetch an X/Twitter status via FxTwitter and shape the result.

    SafetyError (SSRF / host-header smuggling / redirect-validation) MUST
    propagate so the orchestrator returns `unsafe_url_blocked` (exit 4)
    instead of `provider_unavailable` (exit 1).
    """
    client: SafeHttpClient = ctx.get("http_client") or SafeHttpClient()
    target = reader_url(url)
    try:
        response = client.get_text(target, max_bytes=2_000_000)
    except SafetyError:
        raise
    except Exception as exc:
        return _result(
            status="provider_unavailable",
            error={"code": "fxtwitter_request_failed", "message": str(exc)[:200]},
            url=target,
        )
    if response.status_code in {429, 503}:
        return _result(
            status="provider_unavailable",
            error={"code": "fxtwitter_rate_limited", "http_status": response.status_code},
            url=target,
        )
    if response.status_code == 404:
        return _result(
            status="private_or_deleted",
            error={"code": "fxtwitter_not_found"},
            url=target,
        )
    if response.status_code >= 400:
        return _result(
            status="provider_unavailable",
            error={"code": "fxtwitter_http_error", "http_status": response.status_code},
            url=target,
        )
    try:
        data = json.loads(response.text)
    except json.JSONDecodeError:
        return _result(
            status="provider_unavailable",
            error={"code": "fxtwitter_invalid_json"},
            url=target,
        )
    tweet = (data or {}).get("tweet") or {}
    if not tweet:
        return _result(
            status="private_or_deleted",
            error={"code": "fxtwitter_tombstone"},
            url=target,
        )
    text = tweet.get("text", "") or ""
    author = (tweet.get("author") or {}).get("screen_name", "") or ""
    title = f"@{author}" if author else None
    content = text
    return _result(
        status="ok",
        title=title,
        content_markdown=content,
        url=target,
        evidence={"author": author, "id": tweet.get("id")},
    )


def _result(
    *,
    status: str,
    title: str | None = None,
    content_markdown: str | None = None,
    url: str | None = None,
    evidence: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "title": title,
        "content_markdown": content_markdown,
        "provider": "fxtwitter",
        "route": "fxtwitter",
        "evidence": {"provider_url": url, **(evidence or {})},
        "error": error,
    }
