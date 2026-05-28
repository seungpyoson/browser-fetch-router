from __future__ import annotations

import json
import time
from typing import Any, Callable

from browser_fetch_router.http_client import SafeHttpClient

API_BASE = "https://api.browser-use.com/api/v3"
SESSIONS_URL = f"{API_BASE}/sessions"
TERMINAL_STATUSES = frozenset({"stopped", "timed_out", "error"})


def run_task(
    task: str,
    *,
    api_key: str,
    max_steps: int,
    max_duration_sec: int,
    max_cost_usd: float,
    http_client: SafeHttpClient | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    client = http_client or SafeHttpClient()
    headers = {
        "X-Browser-Use-API-Key": api_key,
        "Content-Type": "application/json",
    }
    body = {
        "task": task,
        "model": "bu-mini",
        "keepAlive": False,
        "maxCostUsd": max_cost_usd,
        "proxyCountryCode": None,
        "enableScheduledTasks": False,
        "enableRecording": False,
        "skills": False,
        "agentmail": False,
        "codeMode": False,
        "cacheScript": False,
        "useOwnKey": False,
        "autoHeal": False,
    }
    try:
        response = client.request(
            "POST",
            SESSIONS_URL,
            body=json.dumps(body).encode("utf-8"),
            max_bytes=1_000_000,
            extra_headers=headers,
        )
    except Exception as exc:
        return _provider_error("browser_use_cloud_request_failed", message=str(exc)[:200])

    data = _decode_json(response)
    if response.status_code not in {200, 201}:
        return _http_error(response.status_code, data)

    session_id = data.get("id")
    if not isinstance(session_id, str) or not session_id:
        return _provider_error("browser_use_cloud_missing_session_id")

    step_limit = max(1, int(max_steps))
    latest = data
    deadline = time.monotonic() + max(1, int(max_duration_sec))
    while str(latest.get("status") or "") not in TERMINAL_STATUSES:
        step_count = _step_count(latest)
        if step_count is not None and step_count >= step_limit:
            stopped = _stop_session(client, api_key, session_id)
            if stopped:
                latest = stopped
            evidence = _evidence(session_id, latest)
            evidence["max_steps"] = step_limit
            return _provider_error("browser_use_cloud_max_steps_exceeded", evidence=evidence)
        if time.monotonic() >= deadline:
            stopped = _stop_session(client, api_key, session_id)
            if stopped:
                latest = stopped
            return _provider_error(
                "browser_use_cloud_timeout",
                evidence=_evidence(session_id, latest),
            )
        sleep(min(2.0, max(0.0, deadline - time.monotonic())))
        try:
            response = client.request(
                "GET",
                f"{SESSIONS_URL}/{session_id}",
                max_bytes=1_000_000,
                extra_headers={"X-Browser-Use-API-Key": api_key},
            )
        except Exception as exc:
            stopped = _stop_session(client, api_key, session_id)
            if stopped:
                latest = stopped
            return _provider_error(
                "browser_use_cloud_poll_failed",
                message=str(exc)[:200],
                evidence=_evidence(session_id, latest),
            )
        poll_data = _decode_json(response)
        if response.status_code not in {200, 201}:
            stopped = _stop_session(client, api_key, session_id)
            if stopped:
                latest = stopped
            result = _http_error(response.status_code, poll_data)
            result["evidence"] = _evidence(session_id, latest)
            return result
        latest = poll_data

    remote_status = str(latest.get("status") or "")
    evidence = _evidence(session_id, latest)
    step_count = _step_count(latest)
    if step_count is not None and step_count > step_limit:
        evidence["max_steps"] = step_limit
        return _provider_error("browser_use_cloud_max_steps_exceeded", evidence=evidence)
    if remote_status != "stopped":
        return _provider_error(
            f"browser_use_cloud_{remote_status}",
            evidence=evidence,
        )

    content = _output_text(latest)
    if not content:
        return _provider_error("browser_use_cloud_empty_output", evidence=evidence)

    return {
        "status": "ok",
        "provider": "browser-use-cloud",
        "content_markdown": content,
        "evidence": evidence,
    }


def _decode_json(response: Any) -> dict[str, Any]:
    try:
        data = json.loads(response.text or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _output_text(data: dict[str, Any]) -> str:
    output = data.get("output")
    if isinstance(output, str):
        return output.strip()
    if output is not None:
        return json.dumps(output, sort_keys=True)
    summary = data.get("lastStepSummary")
    return summary.strip() if isinstance(summary, str) else ""


def _step_count(data: dict[str, Any]) -> int | None:
    raw = data.get("stepCount")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _stop_session(client: SafeHttpClient, api_key: str, session_id: str) -> dict[str, Any] | None:
    try:
        response = client.request(
            "POST",
            f"{SESSIONS_URL}/{session_id}/stop",
            max_bytes=1_000_000,
            extra_headers={"X-Browser-Use-API-Key": api_key},
        )
    except Exception:
        return None
    if response.status_code not in {200, 201}:
        return None
    return _decode_json(response)


def _evidence(session_id: str, data: dict[str, Any]) -> dict[str, Any]:
    return {
        "provider": "browser-use-cloud",
        "session_id": session_id,
        "remote_status": data.get("status"),
        "step_count": data.get("stepCount"),
        "total_cost_usd": data.get("totalCostUsd"),
    }


def _http_error(status_code: int, data: dict[str, Any]) -> dict[str, Any]:
    if status_code in {401, 403}:
        return _error(
            "quota_or_key_missing",
            "browser_use_cloud_auth_failed",
            http_status=status_code,
        )
    if status_code == 402:
        return _error(
            "quota_or_key_missing",
            "browser_use_cloud_quota_or_billing",
            http_status=status_code,
        )
    if status_code == 429:
        return _error("rate_limited", "browser_use_cloud_rate_limited", http_status=status_code)
    if status_code == 422:
        return _error(
            "usage_error",
            "browser_use_cloud_usage_error",
            http_status=status_code,
            detail=_safe_detail(data),
        )
    return _error(
        "provider_unavailable",
        "browser_use_cloud_http_error",
        http_status=status_code,
        detail=_safe_detail(data),
    )


def _provider_error(
    code: str,
    *,
    message: str | None = None,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = _error("provider_unavailable", code, message=message)
    if evidence:
        result["evidence"] = evidence
    return result


def _error(status: str, code: str, **fields: Any) -> dict[str, Any]:
    error = {"code": code}
    error.update({key: value for key, value in fields.items() if value is not None})
    return {
        "status": status,
        "provider": "browser-use-cloud",
        "error": error,
    }


def _safe_detail(data: dict[str, Any]) -> Any:
    detail = data.get("detail") or data.get("message") or data.get("error")
    if isinstance(detail, str):
        return detail[:200]
    return detail
