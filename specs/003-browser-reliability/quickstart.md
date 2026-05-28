# Quickstart: Daily-Use Reliability Verification

Run from the repository root unless a command says otherwise.

## 1. Local Tests

```bash
python3 -m pytest tests/browser_fetch_router
```

## 2. Public read-web

```bash
browser-fetch-router read-web https://example.com --json --no-cache
browser-fetch-router read-web https://www.wikipedia.org --json --max-chars 2000 --no-cache
browser-fetch-router read-web https://www.reddit.com/r/python/ --json --no-cache
browser-fetch-router test-acceptance --include-network --json
```

Expected: all valid public-page commands return `status: ok`. Unsafe, blocked, login-walled, and empty pages remain structured failures.

## 3. Paid Parallel Fallback

Supply the key through the environment. Do not write it to files.

```bash
PARALLEL_API_KEY=... browser-fetch-router read-web https://raw.githubusercontent.com/octocat/Hello-World/master/README --allow-paid --json --no-cache
PARALLEL_API_KEY=... browser-fetch-router test-acceptance --include-network --include-paid --json
```

Expected: paid fallback returns `status: ok` when eligible and records provider/cost evidence when available.

## 4. Safe CDP Setup For read-user-tabs

Start a separate Chrome profile. Do not use the normal profile.

```bash
browser-fetch-router read-user-tabs setup --json
browser-fetch-router read-user-tabs setup --launch --start-url https://www.wikipedia.org --json
```

Manual equivalent:

```bash
export BFR_TMPDIR="<tmp-dir-outside-repo>"
BFR_CDP_PROFILE="$(mktemp -d "${BFR_TMPDIR%/}/bfr-cdp-profile.XXXXXX")"
BFR_SCREENSHOT="${BFR_TMPDIR%/}/bfr-active.png"

# macOS:
export CHROME_BIN="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
# Linux:
# export CHROME_BIN="$(command -v google-chrome || command -v chromium || command -v chromium-browser)"

"$CHROME_BIN" \
  --remote-debugging-address=127.0.0.1 \
  --remote-debugging-port=9222 \
  --user-data-dir="$BFR_CDP_PROFILE" \
  --no-first-run \
  --no-default-browser-check \
  https://www.wikipedia.org
```

In another terminal:

```bash
curl -sS http://127.0.0.1:9222/json/version
browser-fetch-router read-user-tabs list --json
browser-fetch-router read-user-tabs list --all --approval-scope exact:list-all-tabs --persist-approval --show-all --json
browser-fetch-router read-user-tabs read active --approval-scope hostname:www.wikipedia.org --max-chars 1000 --json
browser-fetch-router read-user-tabs screenshot active --approval-scope hostname:www.wikipedia.org --output "$BFR_SCREENSHOT" --json
```

Expected: all commands return `status: ok` after CDP is reachable and the needed approval scope is supplied.

Close the temporary Chrome window when done.

## 5. Interactive Browser

Browser Use Cloud live smoke:

```bash
BROWSER_USE_API_KEY=... browser-fetch-router interactive-browser \
  "Open https://example.com and report the page title" \
  --provider cloud \
  --allow-hosted-browser \
  --max-steps 10 \
  --max-cost-usd 0.25 \
  --json
```

Expected: `status: ok`, provider evidence, content containing the page title, step count within cap, and cost within cap.

Browserbase provider and local-discovery checks:

```bash
browser-fetch-router interactive-browser "Open https://example.com and report the page title" --provider browserbase --allow-hosted-browser --json
browser-fetch-router schema --json
```

Expected: Browserbase is live with an end-to-end run; local is absent from daily-use provider choices in schema/help/docs/adapters. If your Browserbase account requires project scoping, set `BROWSERBASE_PROJECT_ID` alongside `BROWSERBASE_API_KEY`.

## 6. Global Install Freshness

Run outside the repository, for example from an external temporary directory:

```bash
command -v browser-fetch-router
browser-fetch-router --help
browser-fetch-router schema --json
browser-fetch-router doctor --json
browser-fetch-router doctor --global-install --json
browser-fetch-router read-web https://example.com --json --no-cache
```

Expected: the global shim resolves to the reviewed package, schema defaults
match the branch, doctor is `ok`, `doctor --global-install --json` reports the
shim path and current schema defaults, and the public smoke succeeds. If the
global command is stale, the verifier returns `stale_global_install` with a
`pipx reinstall --force .` reinstall instruction.

## Latest Local Verification Evidence

- `python3 -m pytest tests/browser_fetch_router -q` -> `757 passed`
- `git diff --check` -> clean
- Tracked-file contributor-path sweep -> no matches
- Outside-repo temporary virtualenv install -> `pip install .`, `browser-fetch-router --help`, `browser-fetch-router schema --json`, and `doctor --global-install --json` passed
- Branch `doctor --global-install --json` verifier first detected the stale
  global shim (`interactive-browser.--max-cost-usd` default `0.05`, missing
  provider capability statuses). After `pipx install --force .`, the same
  verifier passed from a temporary HOME with `status: ok`, cost default `0.25`,
  and cloud provider status `live`.
- Global controlled-HOME adapter smoke passed: `install-agent --all --force
  --json` returned `ok` with Kimi skipped/default-disabled by design, explicit
  `install-agent kimi --force --json` returned `ok`, and global `read-web
  https://example.com --json --no-cache` returned `ok` via `jina-reader`.
- Registry-backed current-package Parallel paid smoke -> `status: ok`, `provider: parallel`, content `Hello World!`
- Live Reddit listing smoke -> `status: ok`, `provider: reddit-json`
- Current-package managed CDP smoke -> `read-user-tabs setup --launch` waited for loopback CDP readiness, `list --show-all` returned the Example Domain tab, `read active` returned Example Domain content, and the temporary Chrome/profile were removed afterward
- Current-package Browser Use Cloud live smoke -> `status: ok`, `provider: browser-use-cloud`, content contained `"Example Domain"`, `remote_status: stopped`, `step_count: 0`, and `total_cost_usd: 0.004490000000000000067446048746`
- Browserbase live smoke -> blocked because the shared key registry/cache has no `BROWSERBASE_API_KEY` entry yet; `BROWSERBASE_PROJECT_ID` is supported as optional account config when present
