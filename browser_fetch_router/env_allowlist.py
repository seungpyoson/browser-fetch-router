from __future__ import annotations

import os

# Provider keys we may expose downstream when explicitly required.
KNOWN_PROVIDER_KEYS = frozenset({
    "PARALLEL_API_KEY",
    "BROWSERBASE_API_KEY",
    "BROWSERBASE_PROJECT_ID",
    "BROWSER_USE_API_KEY",
})

# Agent-context credentials that must NEVER leak to provider adapters or
# spawned subprocesses. Even if the host shell exports them, the CLI strips
# them before invoking providers/browsers.
BLOCKED_AGENT_KEYS = frozenset({
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "CODEX_API_KEY",
    "KIMI_API_KEY",
    "OPENROUTER_API_KEY",
    "MISTRAL_API_KEY",
    "GROQ_API_KEY",
    "TOGETHER_API_KEY",
    "REPLICATE_API_TOKEN",
    "HUGGINGFACE_TOKEN",
    "CO_API_KEY",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "GCP_SERVICE_ACCOUNT",
})

SAFE_BASE_ENV_KEYS = frozenset({
    "HOME",
    "PATH",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "USER",
    "LOGNAME",
    "TZ",
    "BFR_SESSION_ID",
    "BFR_AGENT",
})


# Track which env-vars we've already warned about in THIS process so a
# provider that calls `provider_credential` repeatedly (retry loops, fall-
# back checks, doctor probes) doesn't flood stderr with duplicate
# warnings. Cleared per-process — a fresh CLI invocation always warns
# once on first encounter, which is the desired forensic + operator
# signal.
_WARNED_MALFORMED_CREDS: set[str] = set()


def provider_credential(env_var: str) -> str | None:
    """Read a provider credential from the environment, treating malformed
    bytes as missing.

    A credential containing non-ASCII or non-printable bytes (typo, copy-
    paste artifact, leftover BOM, etc.) cannot be safely placed in an HTTP
    `Authorization` header — the validator in `_validate_extra_headers`
    rightly rejects it with `HostHeaderSmuggling`, which would propagate
    to the user as `unsafe_url_blocked` (exit 4) and falsely blame their
    URL for a config error. Validating at the read boundary instead
    routes the failure through the existing `quota_or_key_missing`
    path with a stderr warning that explains why a present env var is
    being treated as unset. Single source of truth across providers.

    Warnings are deduplicated per-process via `_WARNED_MALFORMED_CREDS`
    so a provider that calls this function multiple times in a single
    invocation (retry, doctor probe, fallback) only surfaces the warning
    once.
    """
    value = os.environ.get(env_var)
    if not value:
        return None
    if not value.isascii() or not value.isprintable():
        if env_var not in _WARNED_MALFORMED_CREDS:
            import sys

            sys.stderr.write(
                f"[bfr] warning: {env_var} contains non-ASCII or non-printable "
                f"bytes; treating as unset\n"
            )
            _WARNED_MALFORMED_CREDS.add(env_var)
        return None
    return value


def provider_env(required_keys: set[str]) -> dict[str, str]:
    """Build a minimal env dict for a provider adapter or subprocess.

    Only base keys plus the explicit `required_keys` (intersected with the
    known provider key allowlist) are included. All agent-context credentials
    are dropped even if the caller asks for them — provider code must never
    see another agent's auth material.
    """
    env = {k: v for k, v in os.environ.items() if k in SAFE_BASE_ENV_KEYS}
    for key in required_keys:
        if key in BLOCKED_AGENT_KEYS:
            continue
        if key in KNOWN_PROVIDER_KEYS and key in os.environ:
            env[key] = os.environ[key]
    return env
