---
name: browser-fetch-router
description: Thin Gemini CLI adapter for the shared browser-fetch-router. Routing, approvals, cache, cost, and lifecycle stay in the shared CLI.
---

# Browser Fetch Router (Gemini CLI adapter)

Invoke the shared `browser-fetch-router` CLI for any web fetch, tab read, or interactive browser task.

## Required env per invocation

- `BFR_AGENT=gemini`
- `BFR_SESSION_ID=<uuid-or-ulid>` — one per Gemini task; reuse across calls.

## Commands

- `browser-fetch-router read-web <url> --json`
- `browser-fetch-router read-user-tabs list --json`
- `browser-fetch-router read-user-tabs read <url-or-tab-id> --json`
- `browser-fetch-router interactive-browser "<task>" --json`

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

## Constraints

- `interactive-browser --provider cloud` is live with `BROWSER_USE_API_KEY`; `--provider browserbase` is live with `BROWSERBASE_API_KEY` and optional `BROWSERBASE_PROJECT_ID`. Both require hosted opt-in. Do not use local interactive mode as a daily-use provider.
- If CDP is unreachable, run `browser-fetch-router read-user-tabs setup --json`; use `--launch` only to start an isolated temporary loopback profile.
- Do not call Gemini's built-in `google_web_search` for arbitrary fetches when `browser-fetch-router` would route the same URL — the shared CLI gives you per-route quality gates and cost ledgering.
- Never embed Gemini API keys in adapter scripts.
