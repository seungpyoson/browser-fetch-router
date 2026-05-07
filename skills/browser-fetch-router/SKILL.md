---
name: browser-fetch-router
description: Use the shared browser-fetch-router CLI for web reads, user-tab reads, and interactive browser tasks with centralized routing, approvals, cache, cost, audit, and cleanup.
---

# Browser Fetch Router

Use the shared `browser-fetch-router` CLI for public web reads, user-tab reads,
and interactive browser tasks. Do not reimplement provider routing or bypass
the CLI with raw HTTP.

## Required Env

- `BFR_AGENT=<agent-name>`
- `BFR_SESSION_ID=<uuid-or-ulid>`

Generate `BFR_SESSION_ID` once per task and reuse it for every CLI call in that
task.

## Commands

- Public URL: `browser-fetch-router read-web <url> --json`
- User tab list: `browser-fetch-router read-user-tabs list --json`
- User tab read: `browser-fetch-router read-user-tabs read <url-or-tab-id> --json`
- Interactive task: `browser-fetch-router interactive-browser "<task>" --json`
- Diagnostics: `browser-fetch-router doctor --json`
- Schema: `browser-fetch-router schema --json`

## Rules

- Do not retry blocked URLs in another tool.
- Do not bypass approval or cost gates.
- Do not store secrets in this skill.
- Hosted paid routes require explicit CLI flags such as `--allow-paid` or
  `--allow-hosted-browser`.

