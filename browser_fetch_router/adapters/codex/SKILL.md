---
name: browser-fetch-router
description: Thin Codex CLI adapter for the shared browser-fetch-router. Provider routing, approvals, cache, cost, and lifecycle live in the shared CLI; never duplicate them here.
---

# Browser Fetch Router (Codex adapter)

Use the shared `browser-fetch-router` CLI for all public web reads, user-tab reads, and interactive browser tasks.

## Required env per invocation

- `BFR_AGENT=codex`
- `BFR_SESSION_ID=<uuid-or-ulid>` — one per Codex task; reuse across all CLI calls.

## Commands

- `browser-fetch-router read-web <url> --json`
- `browser-fetch-router read-user-tabs list --json`
- `browser-fetch-router read-user-tabs read <url-or-tab-id> --json`
- `browser-fetch-router interactive-browser "<task>" --json`
- `browser-fetch-router doctor --json`
- `browser-fetch-router schema --json`

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

## Notes

- Default-deny entries are redacted from tab listings; they cannot be opened without explicit per-URL approval.
- Hosted-paid providers (Parallel Extract, Browserbase, Browser Use Cloud) require explicit `--allow-paid` / `--allow-hosted-browser`.
- Adapter must never store secrets — pass auth only through documented env vars.
