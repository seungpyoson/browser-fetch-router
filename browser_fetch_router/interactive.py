from __future__ import annotations

import os
import re
from decimal import Decimal, InvalidOperation
from math import isfinite
from typing import Any

from browser_fetch_router.cost import CostLedger
from browser_fetch_router.env_allowlist import provider_env
from browser_fetch_router.paths import state_dir
from browser_fetch_router.schema import envelope
from browser_fetch_router.session import current_session_id

HOSTED_BROWSER_MIN_COST_USD = 0.25

# Regex catalogues per action tier.
TIER_C_PATTERNS = [
    re.compile(r"\b(buy|purchase|order|checkout|pay|transfer|wire|send\s+money)\b", re.IGNORECASE),
    re.compile(r"\b(delete|remove|destroy|cancel|terminate|drop|wipe)\b", re.IGNORECASE),
    re.compile(r"\b(login|log\s*in|signin|sign\s*in|signup|sign\s*up|register)\b", re.IGNORECASE),
    re.compile(r"\b(password|2fa|otp|mfa|verify|verification|recover|reset\s+password)\b", re.IGNORECASE),
    re.compile(r"\b(api\s*key|token|credential|secret|private\s+key)\b", re.IGNORECASE),
    re.compile(r"\b(account\s+settings|security\s+settings|billing|subscription)\b", re.IGNORECASE),
]

TIER_B_PATTERNS = [
    re.compile(r"\b(click|tap|press|select|choose|pick)\b", re.IGNORECASE),
    re.compile(r"\b(type|enter|fill|input|paste)\b", re.IGNORECASE),
    re.compile(r"\b(upload|attach|drag)\b", re.IGNORECASE),
]

TIER_A_PATTERNS = [
    re.compile(r"\b(read|view|show|display|screenshot|capture|find|search|look\s+up|extract)\b", re.IGNORECASE),
    re.compile(r"\b(navigate|go\s+to|visit|open\s+(?:page|https?://|www\.|site|website|url|link)|browse)\b", re.IGNORECASE),
]


def classify_action(task: str) -> str:
    """Return one of 'A', 'B', 'C'. Tier C wins over B wins over A.

    Unknown/ambiguous tasks default to C — fail-safe.
    """
    if not task or not task.strip():
        return "C"
    if any(p.search(task) for p in TIER_C_PATTERNS):
        return "C"
    if any(p.search(task) for p in TIER_B_PATTERNS):
        return "B"
    if any(p.search(task) for p in TIER_A_PATTERNS):
        return "A"
    return "C"


def require_action_confirmation(
    tier: str,
    *,
    stdin_is_tty: bool,
    confirmation: str | None,
) -> dict[str, Any]:
    """Block non-interactive Tier C without explicit --confirm-irreversible.

    Returns an envelope-shaped dict on block, or {"status": "ok"} when the
    caller may proceed. Tier A and B always proceed at this layer (provider-
    level approval policy still applies)."""
    if tier in {"A", "B"}:
        return {"status": "ok"}
    if confirmation:
        # Caller explicitly opted in to this irreversible action.
        return {"status": "ok"}
    if stdin_is_tty:
        # The CLI's interactive prompt would handle confirmation. We surface
        # the requirement so the caller knows; live prompting is provider-tier.
        return envelope(
            command="interactive-browser",
            status="approval_required",
            error={
                "code": "tier_c_requires_confirmation",
                "requires": ["--confirm-irreversible <action-id>"],
                "message": "Tier C action requires explicit confirmation",
            },
            approval={"required": True, "scope": None},
        )
    return envelope(
        command="interactive-browser",
        status="approval_required",
        error={
            "code": "tier_c_noninteractive",
            "requires": ["--confirm-irreversible <action-id>"],
            "message": "Tier C action attempted with no TTY; pass --confirm-irreversible to authorize",
        },
        approval={"required": True, "scope": None},
    )


# --------- Provider precondition checks -----------------------------------


def _local_browser_use_available() -> bool:
    """Check whether the `browser-use` distribution is installed.

    Uses `importlib.metadata.distribution` (read-only metadata lookup)
    rather than `importlib.import_module` (a code-execution primitive
    that executes the package's top-level body). The previous
    import-as-probe pattern was a confused-deputy primitive: an
    attacker who could drop a `browser_use.py` shim onto sys.path —
    via CWD when the CLI is launched from a writable directory, via
    PYTHONPATH, or via a malicious sibling package — got arbitrary
    code execution at probe time, BEFORE any sandbox / approval
    check (round-6 r6-06). Distribution-metadata lookup never reads
    or executes the package's source.

    The actual live launcher is still gated by
    `live_local_launch_pending` / `BFR_ENABLE_LIVE_BROWSER`; when that
    gate eventually opens, the import must happen inside the sandbox,
    not at availability probing.
    """
    try:
        from importlib.metadata import PackageNotFoundError, distribution
    except ImportError:
        return False
    try:
        distribution("browser-use")
        return True
    except PackageNotFoundError:
        return False


def _suggested_fallback() -> str | None:
    if "BROWSERBASE_API_KEY" in os.environ and "BROWSERBASE_PROJECT_ID" in os.environ:
        return "browserbase"
    if "BROWSER_USE_API_KEY" in os.environ:
        return "browser-use-cloud"
    return None


def _provider_unavailable(provider: str, tier: str, **evidence: Any) -> dict[str, Any]:
    return envelope(
        command="interactive-browser",
        status="tool_setup_failed",
        error={
            "code": "provider_unavailable",
            "message": "No configured interactive browser provider can launch in this install.",
        },
        evidence={"provider": provider, "tier": tier, **evidence},
    )


def _cost_cap_exceeded(
    *,
    provider: str,
    session_id: str,
    max_cost_usd: float,
    reason: str,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return envelope(
        command="interactive-browser",
        status="cost_cap_exceeded",
        provider=provider,
        error={
            "code": "cost_cap_exceeded",
            "message": "Hosted browser cost cap reached; paid calls are disabled for this session.",
        },
        evidence={
            "provider": provider,
            "session_id": session_id,
            "max_cost_usd": max_cost_usd,
            "reason": reason,
            **(evidence or {}),
        },
    )


def _hosted_cost_cap(value: float) -> float | None:
    if not isfinite(value) or value < 0:
        return None
    return max(HOSTED_BROWSER_MIN_COST_USD, float(value))


def _reserve_hosted_cost(
    ledger: CostLedger,
    session_id: str,
    provider: str,
    amount: float,
    *,
    request_cap: float,
) -> str | bool:
    return ledger.reserve(
        session_id,
        provider,
        amount,
        request_cap=request_cap,
        session_cap=ledger.session_total(session_id) + amount,
        daily_cap=ledger.daily_total() + amount,
    )


def _reported_cost(evidence: Any) -> Decimal | None:
    if not isinstance(evidence, dict):
        return None
    raw = evidence.get("total_cost_usd")
    if raw is None:
        return None
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None
    if not value.is_finite() or value < 0:
        return None
    return value


def _provider_result_envelope(result: dict[str, Any]) -> dict[str, Any]:
    return envelope(
        command="interactive-browser",
        status=result.get("status", "provider_unavailable"),
        route="interactive-browser",
        provider=result.get("provider"),
        content_markdown=result.get("content_markdown"),
        evidence=result.get("evidence"),
        error=result.get("error"),
    )


def _record_reported_hosted_cost(
    *,
    ledger: CostLedger,
    reservation: str | bool,
    session_id: str,
    provider: str,
    reported_cost: Decimal,
    cost_cap: float,
) -> dict[str, Any] | None:
    ledger.release(reservation)
    recorded = _reserve_hosted_cost(
        ledger,
        session_id,
        provider,
        float(reported_cost),
        request_cap=cost_cap,
    )
    if recorded:
        return None
    ledger.disable_session(session_id, "cost_record_failed")
    return _cost_cap_exceeded(
        provider=provider,
        session_id=session_id,
        max_cost_usd=cost_cap,
        reason="cost_record_failed",
    )


def run_interactive_browser(
    task: str,
    *,
    provider: str | None = None,
    allow_hosted_browser: bool = False,
    confirm_irreversible: str | None = None,
    max_steps: int = 10,
    max_duration_sec: int = 300,
    max_cost_usd: float = HOSTED_BROWSER_MIN_COST_USD,
) -> dict[str, Any]:
    """Dispatch an interactive browser task.

    Live provider integration (browser-use local, Browserbase, Browser Use
    Cloud) requires substantial vendor SDKs and careful sandboxing — Task 15
    delivers the policy/precondition layer; the actual SDK launch hooks are
    ready to wire in once the user opts into installing those dependencies.
    """
    tier = classify_action(task)
    confirm = require_action_confirmation(
        tier,
        stdin_is_tty=os.isatty(0) if hasattr(os, "isatty") else False,
        confirmation=confirm_irreversible,
    )
    if confirm.get("status") != "ok":
        return confirm

    # Provider selection.
    selected = provider or "local"
    if selected == "local":
        if not _local_browser_use_available():
            return _provider_unavailable(
                selected,
                tier,
                task_excerpt=task[:120],
                suggested_provider=_suggested_fallback(),
            )
        return _provider_unavailable(
            selected,
            tier,
            limits={
                "max_steps": max_steps,
                "max_duration_sec": max_duration_sec,
                "max_cost_usd": max_cost_usd,
            },
        )

    if selected in {"browserbase", "cloud"} and not allow_hosted_browser:
        return envelope(
            command="interactive-browser",
            status="approval_required",
            error={
                "code": "hosted_browser_requires_opt_in",
                "message": "Pass --allow-hosted-browser to use a hosted provider",
            },
            approval={"required": True, "scope": f"hosted-browser:{selected}"},
        )

    if selected == "browserbase":
        env = provider_env({"BROWSERBASE_API_KEY", "BROWSERBASE_PROJECT_ID"})
        if "BROWSERBASE_API_KEY" not in env or "BROWSERBASE_PROJECT_ID" not in env:
            return envelope(
                command="interactive-browser",
                status="quota_or_key_missing",
                error={"code": "browserbase_credentials_missing"},
            )
        return _provider_unavailable(
            selected,
            tier,
            credentials_present=True,
        )

    if selected == "cloud":
        env = provider_env({"BROWSER_USE_API_KEY"})
        api_key = env.get("BROWSER_USE_API_KEY")
        if not api_key:
            return envelope(
                command="interactive-browser",
                status="quota_or_key_missing",
                error={"code": "browser_use_cloud_key_missing"},
            )
        cost_cap = _hosted_cost_cap(max_cost_usd)
        if cost_cap is None:
            return envelope(
                command="interactive-browser",
                status="usage_error",
                error={"code": "invalid_max_cost_usd", "value": max_cost_usd},
            )
        session_id = current_session_id()
        ledger = CostLedger(state_dir() / "cost.db")
        reservation = _reserve_hosted_cost(
            ledger,
            session_id,
            "browser-use-cloud",
            cost_cap,
            request_cap=cost_cap,
        )
        if not reservation:
            return _cost_cap_exceeded(
                provider="browser-use-cloud",
                session_id=session_id,
                max_cost_usd=cost_cap,
                reason="paid_session_disabled_or_cap_exceeded",
            )

        from browser_fetch_router.providers import browser_use_cloud

        result = browser_use_cloud.run_task(
            task=task,
            api_key=api_key,
            max_steps=max_steps,
            max_duration_sec=max_duration_sec,
            max_cost_usd=cost_cap,
        )
        reported_cost = _reported_cost(result.get("evidence"))
        if reported_cost is not None and reported_cost > Decimal(str(cost_cap)):
            ledger.release(reservation)
            ledger.disable_session(session_id, "provider_overrun")
            return _cost_cap_exceeded(
                provider="browser-use-cloud",
                session_id=session_id,
                max_cost_usd=cost_cap,
                reason="provider_reported_overrun",
                evidence={
                    "reported_total_cost_usd": str(reported_cost),
                    "provider_evidence": result.get("evidence"),
                },
            )
        if result.get("status") != "ok":
            if reported_cost is not None:
                cost_error = _record_reported_hosted_cost(
                    ledger=ledger,
                    reservation=reservation,
                    session_id=session_id,
                    provider="browser-use-cloud",
                    reported_cost=reported_cost,
                    cost_cap=cost_cap,
                )
                if cost_error:
                    return cost_error
            else:
                ledger.release(reservation)
            return _provider_result_envelope(result)
        if reported_cost is not None:
            cost_error = _record_reported_hosted_cost(
                ledger=ledger,
                reservation=reservation,
                session_id=session_id,
                provider="browser-use-cloud",
                reported_cost=reported_cost,
                cost_cap=cost_cap,
            )
            if cost_error:
                return cost_error
        else:
            ledger.release(reservation)
        return _provider_result_envelope(result)

    return envelope(
        command="interactive-browser",
        status="usage_error",
        error={"code": "unknown_provider", "provider": selected},
    )
