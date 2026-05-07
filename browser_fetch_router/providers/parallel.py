from __future__ import annotations

import json
from typing import Any

from browser_fetch_router.env_allowlist import provider_credential
from browser_fetch_router.http_client import SafeHttpClient
from browser_fetch_router.url_safety import SafetyError

PARALLEL_BETA_HEADER = "search-extract-2025-10-10"
PARALLEL_API_URL = "https://api.parallel.ai/v1beta/extract"


def require_key() -> str | None:
    """Return the configured Parallel Extract API key, or None if absent
    or malformed.

    `provider_credential` treats non-ASCII / non-printable bytes as
    missing (with a stderr warning) so a malformed key surfaces through
    the existing `quota_or_key_missing` envelope path instead of
    triggering a `HostHeaderSmuggling` SafetyError from the request-line
    validator. The latter would propagate as `unsafe_url_blocked` (exit
    4), falsely blaming the user's URL for a credential config error.
    """
    return provider_credential("PARALLEL_API_KEY")


def fetch(url: str, ctx: dict[str, Any]) -> dict[str, Any]:
    """Live Parallel Extract POST. Cost reservation/release is handled by the
    caller (read_web orchestration), since reservation requires a session_id
    and route policy that this adapter does not own.

    Raises SafetyError to the orchestrator (NOT swallowed) so SSRF / DNS
    rebinding / host-header-smuggling mid-flight surface as
    `unsafe_url_blocked` (exit 4) and not `provider_unavailable` (exit 1).
    """
    if not ctx.get("allow_paid"):
        return _result(
            "quota_or_key_missing",
            url=url,
            error={"code": "paid_fallback_not_allowed", "message": "Pass --allow-paid to enable Parallel Extract"},
        )
    api_key = require_key()
    if not api_key:
        return _result(
            "quota_or_key_missing",
            url=url,
            error={"code": "parallel_key_missing"},
        )
    client: SafeHttpClient = ctx.get("http_client") or SafeHttpClient()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "parallel-beta": PARALLEL_BETA_HEADER,
        "Content-Type": "application/json",
    }
    body = json.dumps({"url": url}).encode("utf-8")
    try:
        response = client.request(
            "POST",
            PARALLEL_API_URL,
            body=body,
            max_bytes=10_000_000,
            extra_headers=headers,
        )
    except SafetyError:
        # Security exceptions (SSRF, host-header smuggling, redirect to
        # private host) MUST propagate so the orchestrator returns
        # `unsafe_url_blocked` with exit 4. Do not classify as provider
        # error.
        raise
    except Exception as exc:
        return _result(
            "provider_unavailable",
            url=url,
            error={"code": "parallel_request_failed", "message": str(exc)[:200]},
        )
    if response.status_code in {401, 403}:
        return _result(
            "quota_or_key_missing",
            url=url,
            error={"code": "parallel_auth_failed", "http_status": response.status_code},
        )
    if response.status_code == 429:
        return _result(
            "rate_limited",
            url=url,
            error={"code": "parallel_rate_limited"},
        )
    if response.status_code >= 400:
        return _result(
            "provider_unavailable",
            url=url,
            error={"code": "parallel_http_error", "http_status": response.status_code},
        )
    try:
        data = json.loads(response.text)
    except json.JSONDecodeError:
        return _result("provider_unavailable", url=url, error={"code": "parallel_invalid_json"})
    content = (data.get("content") or {}).get("markdown") or data.get("markdown") or ""
    if not content:
        return _result("insufficient_content", url=url, error={"code": "parallel_empty_response"})
    title = (data.get("metadata") or {}).get("title") or None
    return _result("ok", url=url, title=title, content_markdown=content)


def _result(
    status: str,
    *,
    url: str,
    title: str | None = None,
    content_markdown: str | None = None,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "title": title,
        "content_markdown": content_markdown,
        "provider": "parallel",
        "route": "parallel",
        "evidence": {"target": url},
        "error": error,
    }
