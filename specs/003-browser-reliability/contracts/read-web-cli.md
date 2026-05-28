# Contract: read-web Reliability

## Command

```bash
browser-fetch-router read-web <url> --json [--allow-paid] [--no-cache] [--max-chars N]
```

## Success Envelope

- `command`: `read-web`
- `status`: `ok`
- `url`: normalized public URL
- `route`: provider route such as `jina-reader`, `reddit-json`, `parallel`
- `provider`: provider that produced the content
- `content_markdown`: non-empty visible content
- `quality`: quality evidence when applicable
- `evidence.cached`: boolean

## Required Behaviors

- Short but valid public pages return `ok` when they contain clear title/body content and no blocked signals.
- Unsafe URLs, login walls, captcha/JS challenges, blocked pages, and true empty pages remain structured non-OK results.
- Paid fallback is attempted only for eligible generic web insufficient-content results and only when `--allow-paid` is present.
- Parallel fallback uses `POST https://api.parallel.ai/v1/extract` with
  `x-api-key`, `urls`, and `objective`, then parses `results[].full_content`,
  `results[].excerpts`, and URL-specific `errors[]` envelopes.
- Reddit listing URLs and post/comment URLs both return shaped markdown when Reddit JSON contains usable content.

## Required Structured Failures

- `unsafe_url_blocked`: blocked scheme, private address, credentials in URL, or disallowed port.
- `quota_or_key_missing`: paid fallback requested without configured key or paid fallback not allowed.
- `rate_limited`: provider 429.
- `provider_unavailable`: provider request, HTTP, or malformed response failures.
- `insufficient_content`: provider returned no usable public content after quality assessment.
- `auth_required` or browser-required status: user should move to `interactive-browser`.

## Acceptance Commands

```bash
browser-fetch-router read-web https://example.com --json
browser-fetch-router read-web https://www.wikipedia.org --json --max-chars 2000
browser-fetch-router read-web https://www.reddit.com/r/python/ --json
browser-fetch-router test-acceptance --include-network --json
```

Paid fallback smoke, with a real key supplied by environment:

```bash
PARALLEL_API_KEY=... browser-fetch-router read-web https://raw.githubusercontent.com/octocat/Hello-World/master/README --allow-paid --json --no-cache
browser-fetch-router test-acceptance --include-network --include-paid --json
```
