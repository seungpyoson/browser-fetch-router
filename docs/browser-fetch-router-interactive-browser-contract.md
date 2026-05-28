# Browser Fetch Router Interactive Browser Contract

`interactive-browser` is the shared CLI surface for browser tasks that need a
live browser provider. Provider routing, approvals, hosted-browser opt-in, and
cost ledgering stay in the CLI.

## Provider Capability Truth

- `--provider cloud` is live when `BROWSER_USE_API_KEY` is present and
  `--allow-hosted-browser` is supplied.
- `--provider browserbase` is live when `BROWSERBASE_API_KEY` is present and
  `--allow-hosted-browser` is supplied. `BROWSERBASE_PROJECT_ID` is optional
  account configuration and is passed through when present.
- Local interactive mode is not a daily-use provider choice unless it gains an
  end-to-end implementation without additional secret sprawl.

## Browser Use Cloud

Browser Use Cloud requests require explicit hosted-browser opt-in and a cost cap:

```bash
browser-fetch-router interactive-browser \
  "Open https://example.com and report the page title" \
  --provider cloud \
  --allow-hosted-browser \
  --max-steps 10 \
  --max-cost-usd 0.25 \
  --json
```

The CLI applies `--max-cost-usd` as the per-call and per-session cap. Daily
hosted-browser spend is capped separately by
`BFR_HOSTED_BROWSER_DAILY_COST_CAP_USD`, defaulting to `5.0`, so one successful
hosted call does not block later fresh sessions for the rest of the day. Browser
Use Cloud does not expose a create-session `maxSteps` field; the CLI enforces
`--max-steps` by polling provider `stepCount` and stopping a nonterminal session
when the cap is reached.

Secrets must come from documented environment variables or the user's local key
registry. They must not be stored in docs, adapters, tests, or plugin files.
