from __future__ import annotations

import json
from typing import Any

from browser_fetch_router.env_allowlist import provider_credential
from browser_fetch_router.http_client import SafeHttpClient
from browser_fetch_router.url_safety import SafetyError

PARALLEL_API_URL = "https://api.parallel.ai/v1/extract"
PARALLEL_TIMEOUT_SECONDS = 90.0
DEFAULT_EXTRACT_OBJECTIVE = (
    "Extract the main public page content as clean markdown for an AI agent. "
    "Preserve the relevant text and links from the target URL."
)


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
    client: SafeHttpClient = ctx.get("http_client") or SafeHttpClient(
        timeout=PARALLEL_TIMEOUT_SECONDS
    )
    headers = {
        "x-api-key": api_key,
        "Content-Type": "application/json",
    }
    objective = ctx.get("objective") or DEFAULT_EXTRACT_OBJECTIVE
    body = json.dumps({"urls": [url], "objective": objective}).encode("utf-8")
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
            error=_http_error(response, "parallel_auth_failed"),
        )
    if response.status_code == 429:
        return _result(
            "rate_limited",
            url=url,
            error=_http_error(response, "parallel_rate_limited"),
        )
    if response.status_code >= 400:
        return _result(
            "provider_unavailable",
            url=url,
            error=_http_error(response, "parallel_http_error"),
        )
    try:
        data = json.loads(response.text)
    except json.JSONDecodeError:
        return _result("provider_unavailable", url=url, error={"code": "parallel_invalid_json"})
    if not isinstance(data, dict):
        return _result("provider_unavailable", url=url, error={"code": "parallel_invalid_json"})
    result = _first_result_for_url(data, url)
    content = _content_from_result(result)
    if not content:
        extract_error = _extract_error_for_url(data, url)
        if extract_error is not None:
            return _result("provider_unavailable", url=url, error=extract_error)
        return _result("insufficient_content", url=url, error={"code": "parallel_empty_response"})
    title = result.get("title") if isinstance(result, dict) else None
    return _result("ok", url=url, title=title, content_markdown=content)


def _first_result_for_url(data: dict[str, Any], url: str) -> dict[str, Any]:
    results = data.get("results")
    if not isinstance(results, list):
        return {}
    for item in results:
        if isinstance(item, dict) and item.get("url") == url:
            return item
    for item in results:
        if isinstance(item, dict):
            return item
    return {}


def _content_from_result(result: dict[str, Any]) -> str:
    full_content = result.get("full_content")
    if isinstance(full_content, str) and full_content.strip():
        return full_content.strip()
    excerpts = result.get("excerpts")
    if isinstance(excerpts, list):
        parts = [
            part.strip()
            for part in excerpts
            if isinstance(part, str) and part.strip()
        ]
        return "\n\n".join(parts)
    return ""


def _extract_error_for_url(data: dict[str, Any], url: str) -> dict[str, Any] | None:
    selected = _select_error_for_url(data.get("errors"), url)
    if selected is None:
        return None

    error: dict[str, Any] = {"code": "parallel_extract_error"}
    error_type = selected.get("error_type")
    http_status = selected.get("http_status_code")
    message = selected.get("content")
    if isinstance(error_type, str) and error_type:
        error["error_type"] = error_type
    if isinstance(http_status, int):
        error["http_status"] = http_status
    if isinstance(message, str) and message:
        error["message"] = message[:200]
    return error


def _select_error_for_url(errors: Any, url: str) -> dict[str, Any] | None:
    if not isinstance(errors, list):
        return None
    dict_errors = [item for item in errors if isinstance(item, dict)]
    for item in dict_errors:
        if item.get("url") == url:
            return item
    return dict_errors[0] if dict_errors else None


def _http_error(response: Any, code: str) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "http_status": response.status_code}
    try:
        data = json.loads(response.text)
    except json.JSONDecodeError:
        return error
    if not isinstance(data, dict):
        return error
    details = data.get("error")
    if not isinstance(details, dict):
        return error
    message = details.get("message")
    ref_id = details.get("ref_id")
    if isinstance(message, str) and message:
        error["message"] = message[:200]
    if isinstance(ref_id, str) and ref_id:
        error["ref_id"] = ref_id
    return error


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
