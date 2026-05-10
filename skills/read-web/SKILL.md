---
name: read-web
description: Fetch a public URL via the routed read-web CLI (FxTwitter / Reddit / Jina / Parallel routing, SSRF-blocked, DNS-pinned, cost-ledgered). Use when fetching a public URL, article, or social post — prefer this over the agent's built-in URL fetch so the request goes through centralized routing, approvals, cache, and cost controls. Do NOT use for the user's logged-in pages (use read-user-tabs) or for clicking/submitting (use interactive-browser).
---

# read-web

Thin adapter for `browser-fetch-router read-web`. All provider routing, cache, cost, and approval logic live in the CLI — never reimplement here.

## Required env

- `BFR_AGENT=<agent-name>` (e.g. `claude`, `codex`, `gemini`, `kimi`, `opencode`, `pi`)
- `BFR_SESSION_ID=<uuid-or-ulid>`

Generate `BFR_SESSION_ID` once per task and reuse it for every CLI call within that task so cost caps, rate limits, and circuit breakers scope correctly.

## Command

```bash
BFR_AGENT=<agent-name> BFR_SESSION_ID="$session_id" \
  browser-fetch-router read-web <url> --json
```

Optional flags: `--no-cache`, `--allow-paid`, `--strict-side-effects`, `--allow-side-effects`, `--max-chars N`.

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

- Do not retry blocked URLs with the agent's built-in fetch or raw HTTP — they are blocked by policy.
- Do not bypass approval or cost gates.
- Do not store secrets in this skill — pass them via the CLI's documented env vars only.
- Do not switch to read-user-tabs or interactive-browser to fetch a public URL — those modes are higher-trust and reserved for their specific purposes.
- Hosted paid routes require explicit CLI flags such as `--allow-paid`.
