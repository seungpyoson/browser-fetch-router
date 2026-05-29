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

### read-user-tabs CDP Setup

`read-user-tabs` reads from a loopback Chrome CDP endpoint at
`http://127.0.0.1:9222`. Start a separate temporary profile. Do not use the
normal browser profile for CDP. The required flags are
`--remote-debugging-address=127.0.0.1 --remote-debugging-port=9222 --user-data-dir=<temporary-profile>`.

The managed setup helper prints the safe path, and `--launch` starts the
temporary loopback profile:

```bash
browser-fetch-router read-user-tabs setup --json
browser-fetch-router read-user-tabs setup --launch --start-url https://example.com --json
```

```bash
BFR_TMPDIR="${TMPDIR:-/tmp}"
BFR_CDP_PROFILE="$(mktemp -d "${BFR_TMPDIR%/}/bfr-cdp-profile.XXXXXX")"

# macOS:
export CHROME_BIN="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
# Linux:
# export CHROME_BIN="$(command -v google-chrome || command -v chromium || command -v chromium-browser)"

"$CHROME_BIN" \
  --remote-debugging-address=127.0.0.1 \
  --remote-debugging-port=9222 \
  --user-data-dir="$BFR_CDP_PROFILE" \
  --no-first-run \
  --no-default-browser-check
```

### interactive-browser Providers

- `--provider cloud` is live when `BROWSER_USE_API_KEY` is present and
  `--allow-hosted-browser` is supplied.
- `--provider browserbase` is live when `BROWSERBASE_API_KEY` is present and
  `--allow-hosted-browser` is supplied. If your Browserbase account requires a
  project id, also set `BROWSERBASE_PROJECT_ID`.
- Local interactive mode is not advertised as a daily-use provider in this
  build because it would require additional model/provider credentials.

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

To prove the real global command is not stale, run:

```bash
browser-fetch-router doctor --global-install --json
```

The verifier reports the resolved shim path, symlink target when present,
schema defaults, and doctor health. If the global command does not match this
package's expected schema contract it returns `stale_global_install` with a
`pipx reinstall --force .` reinstall instruction.

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
