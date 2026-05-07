from __future__ import annotations

import os
import uuid


def current_session_id(*, optional: bool = False) -> str | None:
    """Return the current session ID, validating against the grammar.

    Single source of truth for "who am I" in the package. Every
    persistent-store consumer (approvals.add_approval, audit, cost
    ledger, lifecycle) trusts this function's return value as a safe
    string. Validating here closes the trust-boundary mismatch that
    used to exist: lifecycle gated session_id at its own boundary
    (`session_registry_path`) but every other consumer accepted the
    raw env var. A grammar-violating BFR_SESSION_ID
    (`../../etc/passwd`, `with space`, `>64 chars`, etc.) used to
    propagate through approvals into approvals.json and contaminate
    forensic trails. Class-I round-17.

    Validation matches lifecycle's `_SESSION_ID_PATTERN` exactly so
    every consumer sees the same gate. Imports are deferred to avoid
    a circular import (lifecycle imports paths which used to import
    session indirectly via deeper modules).
    """
    # Local import: session.py is imported very early in the CLI
    # bootstrap; lifecycle.py imports paths.py which is also early.
    # Keeping this lazy lets either load first.
    from browser_fetch_router.lifecycle import validate_session_id

    value = os.environ.get("BFR_SESSION_ID")
    # `is not None` (not truthy-check) so an explicitly-empty
    # `BFR_SESSION_ID=""` raises InvalidSessionId rather than silently
    # falling through to UUID generation. An empty value is a misconfig
    # — the operator should fix it, not have the CLI hide it.
    if value is not None:
        # Raises InvalidSessionId on grammar violation. The CLI
        # dispatcher catches it and surfaces a usage_error envelope
        # (exit 2) so the user sees the rejected ID instead of a
        # silent skip or a downstream crash.
        return validate_session_id(value)
    if optional:
        return None
    generated = f"bfr-{uuid.uuid4()}"
    # Sanity: the generated form must satisfy the grammar. If it
    # doesn't, that's a coding bug in this function (e.g., somebody
    # changed the prefix to something with a colon). Validate so
    # the bug surfaces here rather than at the next consumer.
    validate_session_id(generated)
    os.environ["BFR_SESSION_ID"] = generated
    return generated


def invoking_agent() -> str | None:
    return os.environ.get("BFR_AGENT")
