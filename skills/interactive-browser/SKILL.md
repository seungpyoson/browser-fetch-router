---
name: interactive-browser
description: Drive an interactive browser to click, type, navigate, or submit on the user's behalf — gated by an action-tier classifier and provider preconditions. Use ONLY when read-web and read-user-tabs cannot accomplish the task — interactive-browser is the highest-trust mode and can write or submit on user-authenticated sites. Do NOT use for read-only fetches (use read-web for public, read-user-tabs for logged-in).
---

# interactive-browser

Thin adapter for `browser-fetch-router interactive-browser`. Action classification, approvals, and provider preconditions live in the CLI — never reimplement here.

## Required env

- `BFR_AGENT=<agent-name>` (e.g. `claude`, `codex`, `gemini`, `kimi`, `opencode`, `pi`)
- `BFR_SESSION_ID=<uuid-or-ulid>` — one per task, reused across all CLI calls in that task.

## Command

```bash
BFR_AGENT=<agent-name> BFR_SESSION_ID="$session_id" \
  browser-fetch-router interactive-browser "<task description>" --json
```

## When to escalate to this from read-* modes

Only when:
- The task requires submitting a form, clicking a button, or any state-changing action.
- read-user-tabs cannot satisfy a read-only task (e.g. content is loaded by interaction the user has not yet performed).

If the task is read-only, prefer read-web (public) or read-user-tabs (logged-in) — they are cheaper and lower-risk.

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | ok |
| 1 | content blocked / not found / paywalled / insufficient |
| 2 | approval_required or approval_denied |
| 3 | tool_setup_failed |
| 4 | unsafe_url_blocked |
| 5 | cost_cap_exceeded or rate_limited |
| 64 | usage_error |
| 70 | internal_error |

## Rules

- Do not call raw HTTP to bypass action-tier approvals.
- Do not chain multiple side-effecting actions in one task without explicit user confirmation.
- Do not use this mode when read-web or read-user-tabs would suffice — escalation has a real cost (approval friction, audit volume, action-tier gating).
- Do not store secrets in this skill — pass them via the CLI's documented env vars only.
- Hosted paid routes require explicit CLI flags such as `--allow-hosted-browser` where relevant.
