---
name: read-user-tabs
description: Read pages already open in the user's real browser via Chrome DevTools Protocol — approval-gated, with default-deny redaction of sensitive sessions. Use when the user references "the page I have open", "what I'm looking at", or a logged-in resource whose session cookies you need. Do NOT use for arbitrary public URLs (use read-web) or for clicking/submitting (use interactive-browser).
---

# read-user-tabs

Thin adapter for `browser-fetch-router read-user-tabs`. CDP lifecycle, approvals, and default-deny redaction live in the CLI — never reimplement here.

## Required env

- `BFR_AGENT=<agent-name>` (e.g. `claude`, `codex`, `gemini`, `kimi`, `opencode`, `pi`)
- `BFR_SESSION_ID=<uuid-or-ulid>` — one per task, reused across all CLI calls in that task.
- `BFR_CDP_URL=<endpoint>` — only if a non-default Chrome DevTools endpoint is in use.

## Commands

```bash
# List currently open tabs (default-deny entries are redacted before they reach you)
BFR_AGENT=<agent-name> BFR_SESSION_ID="$session_id" \
  browser-fetch-router read-user-tabs list --json

# Read a specific tab by URL or tab id
BFR_AGENT=<agent-name> BFR_SESSION_ID="$session_id" \
  browser-fetch-router read-user-tabs read <url-or-tab-id> --json
```

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

- Do not bypass approvals by calling read-web or raw HTTP for a logged-in URL — read-web has no access to the user's session, and switching tools to dodge a redaction is a policy violation.
- Do not retry redacted entries — redaction is the user's policy, not a transient error.
- Do not store secrets in this skill — pass them via the CLI's documented env vars only.
