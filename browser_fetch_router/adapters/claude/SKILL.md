---
name: browser-fetch-router
description: Thin Claude Code adapter for the shared browser-fetch-router CLI. All provider routing, approvals, cache, cost, and lifecycle logic live in the CLI — never duplicate them here.
---

# Browser Fetch Router (Claude Code adapter)

Use the shared `browser-fetch-router` CLI for ALL public web reads, user-tab reads, and interactive browser tasks. This adapter is intentionally thin — it never reimplements provider logic.

## Required env per invocation

- `BFR_AGENT=claude`
- `BFR_SESSION_ID=<uuid-or-ulid>` — one per agent task. Adapters MUST generate this once per task and reuse it for every CLI call within the task so cost caps, rate limits, and circuit breakers can scope correctly.

## Commands

- Public URL: `browser-fetch-router read-web <url> --json`
- User tab list: `browser-fetch-router read-user-tabs list --json` (default-deny entries are redacted)
- User tab read: `browser-fetch-router read-user-tabs read <url-or-tab-id> --json`
- Interactive browser task: `browser-fetch-router interactive-browser "<task>" --json`
- Diagnostics: `browser-fetch-router doctor --json`
- Schema: `browser-fetch-router schema --json`

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

## What you must NOT do

- `interactive-browser --provider cloud` is live with `BROWSER_USE_API_KEY`; `--provider browserbase` is live with `BROWSERBASE_API_KEY` and optional `BROWSERBASE_PROJECT_ID`. Both require hosted opt-in. Do not use local interactive mode as a daily-use provider.
- If CDP is unreachable, run `browser-fetch-router read-user-tabs setup --json`; use `--launch` only to start an isolated temporary loopback profile.
- Do not retry blocked URLs in another tool — they are blocked by policy.
- Do not bypass approvals by calling raw HTTP from Bash.
- Do not store API keys in this adapter — pass them via the CLI's documented env vars only.
