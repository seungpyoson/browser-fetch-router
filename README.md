# Browser Fetch Router

`browser-fetch-router` is a shared, policy-aware browser and web-fetch CLI for
coding agents. It centralizes provider routing, URL safety, approvals, caching,
cost controls, audit logging, and lifecycle cleanup so individual agents do not
reimplement those rules.

## Install

```bash
python3 -m pip install .
browser-fetch-router --help
```

The package exposes these entry points:

- `browser-fetch-router`
- `read-web`
- `read-user-tabs`
- `interactive-browser`

## Agent Usage

Each agent invocation should set:

- `BFR_AGENT=<agent-name>`
- `BFR_SESSION_ID=<uuid-or-ulid>`

Then call the shared CLI:

```bash
browser-fetch-router read-web https://example.com --json
browser-fetch-router read-user-tabs list --json
browser-fetch-router interactive-browser "open example.com and summarize visible text" --json
```

## Tests

```bash
python3 -m pytest tests/browser_fetch_router
```

