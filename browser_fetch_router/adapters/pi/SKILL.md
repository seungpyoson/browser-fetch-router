---
name: browser-fetch-router
description: Thin Pi adapter for the shared browser-fetch-router CLI.
---

# Browser Fetch Router (Pi adapter)

Use the shared `browser-fetch-router` CLI for all web reads, user-tab reads, and interactive browser tasks.

## Required env per invocation

- `BFR_AGENT=pi`
- `BFR_SESSION_ID=<uuid-or-ulid>` — one per task; reuse across calls.

## Commands

- `browser-fetch-router read-web <url> --json`
- `browser-fetch-router read-user-tabs list --json`
- `browser-fetch-router read-user-tabs read <url-or-tab-id> --json`
- `browser-fetch-router interactive-browser "<task>" --json`
- `browser-fetch-router doctor --json`

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

- Pi extensions in `~/.pi/agent/extensions/` may invoke the CLI; the CLI never reads pi config.
- Never embed agent secrets in this adapter.
