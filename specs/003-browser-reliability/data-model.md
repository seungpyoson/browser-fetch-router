# Data Model: Browser Fetch Router Daily-Use Reliability

## FetchResult

Represents one `read-web` result envelope.

- Fields: `schema_version`, `command`, `status`, `url`, `route`, `provider`, `title`, `content_markdown`, `quality`, `evidence`, `error`, `next_path`.
- Validation: `command` is `read-web`; success requires non-empty `content_markdown`; non-success requires a stable `error.code`.
- Relationships: contains `QualityAssessment`; may contain paid provider cost evidence.

## QualityAssessment

Represents content-quality evidence for public URL reads.

- Fields: `word_count`, `has_main_content`, `is_short_valid_content`, `boilerplate_score`, `blocked_signals`, `passes_quality_gate`.
- Validation: blocked/login/captcha/JS challenge signals override short-valid classification.
- State transitions: `raw extraction` -> `quality assessed` -> `ok` or `insufficient_content` or browser-required status.

## ParallelExtractRequest

Represents the paid fallback call to Parallel Extract.

- Fields: `urls`, `objective`, authentication header, provider URL, response `results`, provider `errors`, usage/cost metadata.
- Validation: requires explicit paid opt-in and a valid `PARALLEL_API_KEY`; only safe HTTP(S) public target URLs are allowed.
- Relationships: creates or enriches a `FetchResult`; cost/usage evidence is recorded through existing cost controls.

## RedditListing

Represents normalized Reddit listing content.

- Fields: listing title, entry title, author, permalink/url, score/comment metadata when available, excerpt/selftext.
- Validation: dict-style listing responses require `data.children` list entries; list-style post/comment responses keep existing behavior.
- Relationships: shaped into `FetchResult.content_markdown` by `providers/reddit.py`.

## CdpSetupGuide

Represents user-facing setup guidance for `read-user-tabs`.

- Fields: loopback address, port, temporary profile path, browser launch command, verification command, cleanup instruction.
- Validation: guidance must use `127.0.0.1` and an isolated temporary profile; no instruction may expose a normal profile or remote interface by default.
- Relationships: surfaced from docs, adapter text, schema/help, and `cdp_unreachable` error evidence.

## InteractiveProviderCapability

Represents one `interactive-browser` provider's truthful status.

- Fields: provider id (`browserbase`, `cloud` for daily-use choices), display name, credential requirements, opt-in requirements, live status, cost cap, smoke command.
- Validation: providers marked live must have an end-to-end CLI smoke; providers without a live path must be absent from daily-use discovery.
- Relationships: drives CLI choices/help, schema metadata, docs, and adapter guidance.

## GlobalInstallState

Represents the real installed CLI state.

- Fields: command path, symlink target, package location, version/schema defaults, adapter destinations, doctor status, outside-repo execution result.
- Validation: expected branch/schema defaults must match actual global output; stale installs fail verification.
- Relationships: consumed by maintainer quickstart and release verification.

## VerificationMatrix

Represents the release checklist across surfaces.

- Fields: surface, required unit tests, required CLI tests, optional live smoke, docs/adapters touched, expected structured failures.
- Validation: every user story maps to at least one failing regression test and one final verification command.
- Relationships: feeds `tasks.md`, PR review evidence, and the epic issue checklist.
