# Browser Fetch Router Interactive Browser Contract

`interactive-browser` is the shared CLI surface for browser tasks that need a
live browser provider. Provider routing, approvals, hosted-browser opt-in, and
cost ledgering stay in the CLI.

## Provider Capability Truth

- `--provider cloud` is live when `BROWSER_USE_API_KEY` is present and
  `--allow-hosted-browser` is supplied.
- `--provider browserbase` is unavailable until live launch support exists.
- `--provider local` is unavailable until live launch support exists.
- Unavailable providers must return `tool_setup_failed` with
  `error.code: provider_unavailable`.

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

The CLI applies `--max-cost-usd` as the request, session, and daily cap until
separate knobs exist. Browser Use Cloud does not expose a create-session
`maxSteps` field; the CLI enforces `--max-steps` by polling provider
`stepCount` and stopping a nonterminal session when the cap is reached.

Secrets must come from documented environment variables or the user's local key
registry. They must not be stored in docs, adapters, tests, or plugin files.
