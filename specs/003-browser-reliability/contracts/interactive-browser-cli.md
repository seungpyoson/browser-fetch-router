# Contract: interactive-browser Provider Truthfulness

## Command

```bash
browser-fetch-router interactive-browser "<task>" --json [--provider local|browserbase|cloud] [--allow-hosted-browser] [--max-cost-usd N]
```

## Required Behaviors

- Tier C tasks still require explicit irreversible confirmation.
- Hosted providers still require `--allow-hosted-browser`.
- Every provider exposed as live in schema/help/docs/adapters must execute an end-to-end task with credentials/dependencies and return a structured result.
- Providers that are not implemented must be marked unavailable/pending consistently in schema/help/docs/adapters and return `tool_setup_failed/provider_unavailable` with actionable evidence.

## Browser Use Cloud Live Provider

With `BROWSER_USE_API_KEY` present and hosted browser opt-in:

- `status`: `ok` for a simple page-title task
- `provider`: `browser-use-cloud`
- `content_markdown`: non-empty task result
- `evidence.remote_status`: terminal remote status
- `evidence.total_cost_usd`: provider-reported cost when available
- Cost ledger records the actual reported cost or disables the session on overrun
- The single `--max-cost-usd` value is applied as the request, session, and daily hosted-browser cap until separate knobs exist.

## Browserbase And Local Provider Contract

Browserbase and local provider entries must choose one state:

- `live`: end-to-end execution path exists and has tests/smoke evidence.
- `unavailable`: provider remains selectable only as an explicit unavailable provider with docs/schema describing missing live launch support.
- `hidden`: provider is removed from daily-use discovery until implemented.

Stubs must not be represented as daily-use ready providers.

## Acceptance Commands

```bash
BROWSER_USE_API_KEY=... browser-fetch-router interactive-browser "Open https://example.com and report the page title" --provider cloud --allow-hosted-browser --max-cost-usd 0.25 --json
browser-fetch-router interactive-browser "Open https://example.com and report the page title" --provider browserbase --allow-hosted-browser --json
browser-fetch-router interactive-browser "Open https://example.com and report the page title" --provider local --json
browser-fetch-router schema --json
```
