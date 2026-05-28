---
name: browser-fetch-router
description: Thin OpenCode adapter for the shared browser-fetch-router CLI.
---

# Browser Fetch Router (OpenCode adapter)

Use the shared `browser-fetch-router` CLI for all web reads, user-tab reads, and interactive browser tasks. Provider routing, cache, cost, and approvals live in the CLI.

## Required env per invocation

- `BFR_AGENT=opencode`
- `BFR_SESSION_ID=<uuid-or-ulid>` — one per task; reuse across calls.

## Commands

- `browser-fetch-router read-web <url> --json`
- `browser-fetch-router read-user-tabs list --json`
- `browser-fetch-router read-user-tabs read <url-or-tab-id> --json`
- `browser-fetch-router interactive-browser "<task>" --json`
- `browser-fetch-router doctor --json`

## User Tab CDP Setup

- `read-user-tabs` requires loopback Chrome CDP at `http://127.0.0.1:9222`.
- Start Chrome/Chromium with `--remote-debugging-address=127.0.0.1 --remote-debugging-port=9222 --user-data-dir=<temporary-profile>`.
- Do not use the normal browser profile for CDP.

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

- `interactive-browser --provider cloud` is live with `BROWSER_USE_API_KEY`; `browserbase` and `local` are unavailable until live launch support exists.
- Never embed agent secrets in this adapter.
