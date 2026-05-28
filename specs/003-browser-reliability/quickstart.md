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
PARALLEL_API_KEY=... browser-fetch-router read-web https://example.com --allow-paid --json --no-cache
PARALLEL_API_KEY=... browser-fetch-router test-acceptance --include-network --include-paid --json
```

Expected: paid fallback returns `status: ok` when eligible and records provider/cost evidence when available.

## 4. Safe CDP Setup For read-user-tabs

Start a separate Chrome profile. Do not use the normal profile.

```bash
mkdir -p /private/tmp/bfr-cdp-profile

"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --remote-debugging-address=127.0.0.1 \
  --remote-debugging-port=9222 \
  --user-data-dir=/private/tmp/bfr-cdp-profile \
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
browser-fetch-router read-user-tabs screenshot active --approval-scope hostname:www.wikipedia.org --output /private/tmp/bfr-active.png --json
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
  --max-cost-usd 0.25 \
  --json
```

Expected: `status: ok`, provider evidence, content containing the page title, and cost within cap.

Browserbase/local provider checks:

```bash
browser-fetch-router interactive-browser "Open https://example.com and report the page title" --provider browserbase --allow-hosted-browser --json
browser-fetch-router interactive-browser "Open https://example.com and report the page title" --provider local --json
browser-fetch-router schema --json
```

Expected: each provider is either live with an end-to-end run or consistently marked unavailable/pending in schema/help/docs/adapters.

## 6. Global Install Freshness

Run outside the repository, for example from `/private/tmp`:

```bash
command -v browser-fetch-router
browser-fetch-router --help
browser-fetch-router schema --json
browser-fetch-router doctor --json
browser-fetch-router read-web https://example.com --json --no-cache
```

Expected: the global shim resolves to the reviewed package, schema defaults match the branch, doctor is `ok`, and the public smoke succeeds.
