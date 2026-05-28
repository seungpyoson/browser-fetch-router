# Contract: interactive-browser Provider Truthfulness

## Command

```bash
browser-fetch-router interactive-browser "<task>" --json [--provider browserbase|cloud] [--allow-hosted-browser] [--max-cost-usd N]
```

## Required Behaviors

- Tier C tasks still require explicit irreversible confirmation.
- Hosted providers still require `--allow-hosted-browser`.
- Every provider exposed as live in schema/help/docs/adapters must execute an end-to-end task with credentials/dependencies and return a structured result.
- Providers that are not implemented must not be advertised as daily-use provider choices in schema/help/docs/adapters.

## Browser Use Cloud Live Provider

With `BROWSER_USE_API_KEY` present and hosted browser opt-in:

- `status`: `ok` for a simple page-title task
- `provider`: `browser-use-cloud`
- `content_markdown`: non-empty task result
- `evidence.remote_status`: terminal remote status
- `evidence.step_count`: provider-reported step count, no greater than `--max-steps`
- `evidence.total_cost_usd`: provider-reported cost when available
- Browser Use Cloud v3 does not expose a create-session `maxSteps` request field; the CLI enforces `--max-steps` by polling `stepCount` and stopping a nonterminal cloud session when the cap is reached.
- Cost ledger records the actual reported cost or disables the session on overrun
- The single `--max-cost-usd` value is applied as the request, session, and daily hosted-browser cap until separate knobs exist.

## Browserbase And Local Mode Contract

Browserbase provider entries must choose one state:

- `live`: end-to-end execution path exists and has tests/smoke evidence.
- `hidden`: provider is removed from daily-use discovery until implemented.

Local mode is hidden from daily-use discovery until it has an end-to-end implementation without additional secret sprawl. Stubs must not be represented as daily-use ready providers.

## Acceptance Commands

```bash
BROWSER_USE_API_KEY=... browser-fetch-router interactive-browser "Open https://example.com and report the page title" --provider cloud --allow-hosted-browser --max-steps 10 --max-cost-usd 0.25 --json
BROWSERBASE_API_KEY=... browser-fetch-router interactive-browser "Open https://example.com and report the page title" --provider browserbase --allow-hosted-browser --max-steps 10 --max-cost-usd 0.25 --json
browser-fetch-router schema --json
```

If the Browserbase account requires project scoping, set
`BROWSERBASE_PROJECT_ID` alongside `BROWSERBASE_API_KEY`.
