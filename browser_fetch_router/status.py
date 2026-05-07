from __future__ import annotations

STATUS_EXIT_CODES: dict[str, int] = {
    "ok": 0,
    "blocked": 1,
    "private_or_deleted": 1,
    "blocked_needs_browser": 1,
    "auth_required": 1,
    "paywalled": 1,
    "insufficient_content": 1,
    "provider_unavailable": 1,
    "captcha_required": 1,
    "tool_setup_failed": 3,
    "quota_or_key_missing": 3,
    "unsafe_url_blocked": 4,
    "approval_required": 2,
    "approval_denied": 2,
    "cost_cap_exceeded": 5,
    "rate_limited": 5,
    "usage_error": 64,
    "internal_error": 70,
    # SIGINT / SIGTERM during a long-running operation (interactive-browser,
    # cleanup, anything taking measurable wall time). Exit 130 follows POSIX
    # SIGINT convention. Recorded so the audit log distinguishes "user
    # cancelled mid-action" from "Python crashed" — relevant for forensics
    # when an interrupted action had already committed side effects.
    "interrupted": 130,
}
