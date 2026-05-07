from __future__ import annotations

import os
import re
from typing import Any

from browser_fetch_router.env_allowlist import provider_env
from browser_fetch_router.schema import envelope

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
    re.compile(r"\b(navigate|go\s+to|visit|open\s+page|browse)\b", re.IGNORECASE),
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


def run_interactive_browser(
    task: str,
    *,
    provider: str | None = None,
    allow_hosted_browser: bool = False,
    confirm_irreversible: str | None = None,
    max_steps: int = 10,
    max_duration_sec: int = 300,
    max_cost_usd: float = 0.05,
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
            return envelope(
                command="interactive-browser",
                status="tool_setup_failed",
                error={
                    "code": "browser_use_preconditions_failed",
                    "message": "browser-use is not installed. Install it or pick --provider browserbase / cloud (with --allow-hosted-browser)",
                    "suggested_provider": _suggested_fallback(),
                },
                evidence={"tier": tier, "task_excerpt": task[:120]},
            )
        # Live local launch is intentionally deferred to a follow-up because
        # spawning Chrome with a tool-owned profile + lifecycle registry
        # requires careful integration testing.
        return envelope(
            command="interactive-browser",
            status="tool_setup_failed",
            error={
                "code": "live_local_launch_pending",
                "message": "browser-use local launcher is wired but not enabled in v1; enable with BFR_ENABLE_LIVE_BROWSER=1 once the sandbox probe in doctor reports OK",
            },
            evidence={"tier": tier, "limits": {"max_steps": max_steps, "max_duration_sec": max_duration_sec, "max_cost_usd": max_cost_usd}},
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
        return envelope(
            command="interactive-browser",
            status="tool_setup_failed",
            error={"code": "browserbase_launch_pending", "message": "Browserbase remote launcher wired but disabled until live integration test passes"},
            evidence={"tier": tier, "credentials_present": True},
        )

    if selected == "cloud":
        env = provider_env({"BROWSER_USE_API_KEY"})
        if "BROWSER_USE_API_KEY" not in env:
            return envelope(
                command="interactive-browser",
                status="quota_or_key_missing",
                error={"code": "browser_use_cloud_key_missing"},
            )
        return envelope(
            command="interactive-browser",
            status="tool_setup_failed",
            error={"code": "browser_use_cloud_launch_pending", "message": "Browser Use Cloud launcher wired but disabled until live integration test passes"},
            evidence={"tier": tier, "credentials_present": True},
        )

    return envelope(
        command="interactive-browser",
        status="usage_error",
        error={"code": "unknown_provider", "provider": selected},
    )
