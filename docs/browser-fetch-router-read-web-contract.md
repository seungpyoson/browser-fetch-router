# Browser Fetch Router read-web Contract

`browser-fetch-router read-web <url> --json` reads public HTTP(S) pages through
the shared CLI provider router. Agent adapters must call this CLI and must not
duplicate provider routing, quality gates, paid fallback policy, cache, or
credential handling.

## Public Page Quality

`read-web` returns `status: ok` when the provider returns usable public page
content. Usable content includes concise public pages when all of these are
true:

- the extracted text has enough visible content to be useful;
- no login, captcha, JavaScript challenge, or other blocked signal is detected;
- the extraction is not dominated by boilerplate;
- the provider result includes the quality evidence that made the decision.

Short valid pages are not rejected solely because they have fewer than 80 words.
For these pages, the envelope includes:

```json
{
  "quality": {
    "is_short_valid_content": true,
    "passes_quality_gate": true
  }
}
```

## Structured Failures

The router must keep explicit non-OK statuses for cases that are not readable
public content:

- `unsafe_url_blocked` for blocked schemes, private addresses, embedded
  credentials, disallowed ports, or redirect safety failures;
- `auth_required` for login walls;
- `blocked_needs_browser` for captcha or JavaScript challenge pages;
- `insufficient_content` for true empty or low-value extraction results;
- `quota_or_key_missing` when paid fallback would be required but the caller did
  not opt in or no provider key is configured;
- `provider_unavailable` and `rate_limited` for provider-side failures.

Browser-required statuses must point to `interactive-browser` through
`next_path` when that is the correct next surface.

## Paid Fallback

Parallel Extract is a paid fallback only for generic public web reads whose
free provider result is `insufficient_content`. It must not run unless the user
passes `--allow-paid` and the configured cost policy allows the request. API
keys must be supplied through documented environment variables only; they must
not appear in docs, adapters, tests, logs, or issues.

The Parallel call uses the current Extract API contract:

- `POST https://api.parallel.ai/v1/extract`
- `x-api-key: $PARALLEL_API_KEY`
- JSON body with `urls: [target_url]` and an extraction `objective`
- success content from `results[].full_content` or joined `results[].excerpts`
- structured failures for HTTP errors, rate limits, invalid JSON, empty
  results, missing keys, and URL-specific `errors[]` entries

## Verification

Minimum verification for this contract:

```bash
python3 -m pytest tests/browser_fetch_router/test_browser_reliability_providers.py tests/browser_fetch_router/test_quality.py
BFR_TEST_HOME="$(mktemp -d)"
HOME="$BFR_TEST_HOME" python3 -m browser_fetch_router read-web https://example.com --json --no-cache
HOME="$BFR_TEST_HOME" python3 -m browser_fetch_router test-acceptance --include-network --json
```
