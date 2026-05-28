from __future__ import annotations

import asyncio
from typing import Any


def run_task(
    task: str,
    *,
    api_key: str,
    project_id: str | None = None,
    model_name: str,
    max_steps: int,
    max_duration_sec: int,
) -> dict[str, Any]:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(
            _run_task_async(
                task=task,
                api_key=api_key,
                project_id=project_id,
                model_name=model_name,
                max_steps=max_steps,
                max_duration_sec=max_duration_sec,
            )
        )
    else:
        return _error("provider_unavailable", "browserbase_event_loop_unavailable")


async def _run_task_async(
    *,
    task: str,
    api_key: str,
    project_id: str | None,
    model_name: str,
    max_steps: int,
    max_duration_sec: int,
) -> dict[str, Any]:
    try:
        from stagehand import AsyncStagehand
    except Exception as exc:
        return _error(
            "provider_unavailable",
            "browserbase_stagehand_missing",
            message=str(exc)[:200],
        )

    session = None
    try:
        async with AsyncStagehand(
            browserbase_api_key=api_key,
            browserbase_project_id=project_id,
            timeout=float(max(1, int(max_duration_sec))),
            max_retries=0,
        ) as client:
            session = await client.sessions.start(model_name=model_name)
            try:
                response = await session.execute(
                    execute_options={
                        "instruction": task,
                        "max_steps": max(1, int(max_steps)),
                    },
                    agent_config={"model": model_name},
                    timeout=float(max(1, int(max_duration_sec))),
                )
                result = response.data.result
                message = getattr(result, "message", "") or ""
                success = bool(getattr(result, "success", False))
                evidence = {
                    "provider": "browserbase",
                    "session_id": getattr(session, "id", None),
                    "model_name": model_name,
                    "completed": getattr(result, "completed", None),
                    "success": success,
                    "action_count": len(getattr(result, "actions", []) or []),
                }
                usage = getattr(result, "usage", None)
                if usage is not None:
                    evidence["usage"] = usage.to_dict() if hasattr(usage, "to_dict") else usage
                if not success:
                    return _error(
                        "provider_unavailable",
                        "browserbase_task_failed",
                        message=message[:200] if message else None,
                        evidence=evidence,
                    )
                if not message:
                    return _error(
                        "provider_unavailable",
                        "browserbase_empty_output",
                        evidence=evidence,
                    )
                return {
                    "status": "ok",
                    "provider": "browserbase",
                    "content_markdown": message.strip(),
                    "evidence": evidence,
                }
            finally:
                try:
                    await session.end()
                except Exception:
                    pass
    except Exception as exc:
        return _error(
            _status_for_exception(exc),
            _code_for_exception(exc),
            message=str(exc)[:200],
            evidence={
                "provider": "browserbase",
                "session_id": getattr(session, "id", None),
            },
        )


def _status_for_exception(exc: BaseException) -> str:
    status_code = getattr(exc, "status_code", None)
    if status_code in {401, 403}:
        return "quota_or_key_missing"
    if status_code == 402:
        return "quota_or_key_missing"
    if status_code == 429:
        return "rate_limited"
    if status_code == 422:
        return "usage_error"
    return "provider_unavailable"


def _code_for_exception(exc: BaseException) -> str:
    status_code = getattr(exc, "status_code", None)
    if status_code in {401, 403}:
        return "browserbase_auth_failed"
    if status_code == 402:
        return "browserbase_quota_or_billing"
    if status_code == 429:
        return "browserbase_rate_limited"
    if status_code == 422:
        return "browserbase_usage_error"
    return "browserbase_request_failed"


def _error(
    status: str,
    code: str,
    *,
    message: str | None = None,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    error = {"code": code}
    if message:
        error["message"] = message
    result: dict[str, Any] = {
        "status": status,
        "provider": "browserbase",
        "error": error,
    }
    if evidence:
        result["evidence"] = evidence
    return result
