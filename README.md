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

## Agent Adapter Install

Install thin agent adapters with the shared CLI:

```bash
browser-fetch-router install-agent --all --json
browser-fetch-router install-agent pi --json
browser-fetch-router install-agent --select codex,gemini,opencode --json
```

The supported/default agent matrix, Pi migration note, Kimi inheritance caveat,
environment override behavior, and `--adapter-path` rules are documented in
[`docs/browser-fetch-router-install-agent-contract.md`](docs/browser-fetch-router-install-agent-contract.md).

## Tests

```bash
python3 -m pytest tests/browser_fetch_router
```

For contributor readiness, also verify package installability from outside the
repository:

```bash
python3 -m pip install <checkout-path>
browser-fetch-router --help
```

Generated virtualenvs, caches, bytecode, and package metadata are ignored by the
repository. Keep `git status --short` clean after running the documented flow.
