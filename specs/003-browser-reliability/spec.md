# Feature Specification: Browser Fetch Router Daily-Use Reliability

**Feature Branch**: `003-browser-reliability`
**Created**: 2026-05-28
**Status**: Draft
**Input**: User description: "Browser Fetch Router daily-use reliability root-cause fixes for read-web, read-user-tabs setup, interactive providers, and global install verification"

## User Scenarios & Testing *(mandatory)*

<!--
  IMPORTANT: User stories should be PRIORITIZED as user journeys ordered by importance.
  Each user story/journey must be INDEPENDENTLY TESTABLE - meaning if you implement just ONE of them,
  you should still have a viable MVP (Minimum Viable Product) that delivers value.

  Assign priorities (P1, P2, P3, etc.) to each story, where P1 is the most critical.
  Think of each story as a standalone slice of functionality that can be:
  - Developed independently
  - Tested independently
  - Deployed independently
  - Demonstrated to users independently
-->

### User Story 1 - Public read-web succeeds for valid public pages (Priority: P1)

As an agent using Browser Fetch Router for ordinary public-web reading, I need valid public pages to return useful content or a precise structured failure, so I can rely on `read-web` as the default daily surface.

**Why this priority**: Public URL reading is the core skill surface. A normal page such as `https://example.com` currently fails even though upstream content exists, and the paid fallback path also fails with a request-shape mismatch when credentials are present.

**Independent Test**: Run targeted public CLI tests and `browser-fetch-router test-acceptance --include-network --json`; `example-read-network` must return `ok`, valid short pages must pass without paid fallback, and a real Parallel key must make paid fallback return structured content when free providers cannot satisfy the quality gate.

**Acceptance Scenarios**:

1. **Given** `https://example.com` and no paid fallback authorization, **When** `browser-fetch-router read-web https://example.com --json` runs, **Then** the command returns `status: ok` with non-empty visible content and does not require paid fallback.
2. **Given** a valid `PARALLEL_API_KEY` and `--allow-paid`, **When** the free providers cannot satisfy the public-page quality gate, **Then** the Parallel fallback uses the currently supported API request shape and returns structured extracted content or a provider-specific error without a traceback.
3. **Given** a genuinely blocked, empty, or login-walled public page, **When** `read-web` cannot extract usable public content, **Then** the failure reason identifies the gate that failed and the next path remains explicit.
4. **Given** `browser-fetch-router test-acceptance --include-network --json`, **When** the public network acceptance cases run, **Then** every expected public success case returns `ok`.

---

### User Story 2 - Reddit routes handle listings and posts separately (Priority: P1)

As an agent reading Reddit through `read-web`, I need subreddit listings and post/comment pages to both work through the Reddit provider, so I can use the same command for common Reddit URLs without guessing URL shape limitations.

**Why this priority**: Reddit is a built-in provider route. Post/comment URLs work, but subreddit listing URLs currently return `reddit_empty_listing` even though Reddit JSON includes listing children.

**Independent Test**: Run provider-level and CLI-level Reddit tests for a subreddit listing URL and a post/comment URL. The listing path must shape dict-style Reddit listing JSON; the post path must keep passing.

**Acceptance Scenarios**:

1. **Given** `https://www.reddit.com/r/python/`, **When** `browser-fetch-router read-web ... --json` runs, **Then** it returns `status: ok` with listing entries from `data.children`.
2. **Given** a Reddit post/comment URL, **When** the same command runs, **Then** the existing post/comment extraction behavior remains `status: ok`.
3. **Given** a Reddit response with no listing children, **When** the provider shapes the result, **Then** it returns a structured empty-listing failure instead of conflating the case with unsupported response shape.

---

### User Story 3 - User-tab reading has a no-mistakes setup path (Priority: P2)

As an agent or user who wants to read already-open browser tabs, I need the CLI, docs, and installed adapters to tell me exactly how to start a safe loopback CDP browser session and verify it, so `read-user-tabs` failure is actionable rather than opaque.

**Why this priority**: `read-user-tabs` works once a loopback Chrome CDP endpoint exists, but the global help, README, and adapter surfaces do not expose the setup path. Users only see `cdp_unreachable`.

**Independent Test**: From a clean machine state with no listener on `127.0.0.1:9222`, run the documented setup flow from user-facing docs or adapter text, verify `/json/version`, then run `read-user-tabs list`, `read active`, and `screenshot active` against an isolated temporary Chrome profile.

**Acceptance Scenarios**:

1. **Given** no CDP listener, **When** `browser-fetch-router read-user-tabs list --json` runs, **Then** the structured error includes the exact safe setup command or a documented pointer to it.
2. **Given** the documented temporary Chrome CDP command has been started, **When** `read-user-tabs list --json` runs, **Then** it returns `status: ok`.
3. **Given** an approved host scope for the active tab, **When** `read-user-tabs read active --json` runs, **Then** it returns visible page content and preserves approval enforcement.
4. **Given** an approved screenshot scope and output path, **When** `read-user-tabs screenshot active --json` runs, **Then** it writes the screenshot and reports the file path.

---

### User Story 4 - Interactive browser providers are truthful and live (Priority: P2)

As an agent invoking `interactive-browser`, I need exposed providers to either run end-to-end or be clearly marked unavailable, so I do not treat stubbed provider names as reliable daily-use surfaces.

**Why this priority**: `interactive-browser --provider cloud` has live evidence with Browser Use Cloud credentials, but `local` and `browserbase` currently return `provider_unavailable` even when dependencies or credentials are present. The CLI/schema/help should not imply stubbed providers are ready.

**Independent Test**: Run CLI tests for absent credentials, present Browser Use Cloud credentials, present Browserbase credentials, and local provider dependency states. Each advertised provider either completes a live smoke within the configured cost cap or is marked unavailable consistently in schema, help, docs, and adapter text.

**Acceptance Scenarios**:

1. **Given** a valid Browser Use Cloud API key and cost cap, **When** `interactive-browser --provider cloud "Open example.com and report the page title" --json` runs, **Then** it returns `status: ok`, content containing the page title, provider evidence, and ledger cost within the cap.
2. **Given** no provider credentials, **When** `interactive-browser` is invoked noninteractively, **Then** it returns the existing approval or credential gate without launching a task.
3. **Given** Browserbase credentials are present, **When** the Browserbase provider is advertised as available, **Then** the provider must execute a real Browserbase run; otherwise Browserbase must be marked unavailable/pending in all user-facing discovery surfaces.
4. **Given** the local provider dependencies are installed, **When** the local provider is advertised as available, **Then** it must execute a real local browser-use run; otherwise local must be marked unavailable/pending in all user-facing discovery surfaces.

---

### User Story 5 - Global install verification proves the real user CLI is current (Priority: P3)

As a maintainer validating daily use after release, I need a repeatable global install verification that proves the actual shim, schema, adapters, and package version match the reviewed branch, so stale pipx/global state is not mistaken for product failure.

**Why this priority**: The real global shim can point at an older pipx environment while branch and temp-venv tests pass. This makes live smoke results ambiguous.

**Independent Test**: Reinstall from the reviewed branch into the global target, then run `command -v`, symlink inspection, `doctor`, `schema`, `install-agent`, adapter content checks, and a public `read-web` smoke from outside the repository.

**Acceptance Scenarios**:

1. **Given** a global `browser-fetch-router` shim, **When** the verification command inspects it, **Then** the report identifies the target venv/package and the schema defaults expected for the branch.
2. **Given** stale global schema defaults, **When** global verification runs, **Then** it fails with a clear stale-install finding.
3. **Given** global reinstall has completed, **When** adapters are installed for supported agents, **Then** all installed adapter files contain the shared CLI invocation and no embedded secrets.

### Edge Cases

- Valid public pages with fewer than the current minimum word-count threshold must not be rejected solely for being short when they contain clear visible page content.
- Genuinely empty, blocked, bot-gated, login-walled, or unsafe URLs must remain structured failures and must not be forced into `ok`.
- Paid fallback must not run unless the user explicitly allows paid usage and the cost cap permits it.
- Parallel HTTP 4xx, 429, malformed responses, and missing API keys must map to stable structured errors.
- Reddit dict-style listing JSON and list-style post/comment JSON must both be supported without regressing either path.
- CDP setup guidance must be safe by default: loopback address, dedicated temporary profile, and no instruction to expose a normal profile over remote debugging.
- `read-user-tabs` must preserve approval-scope checks after CDP becomes reachable.
- Interactive providers with present credentials must not be reported as live unless a real vendor or local execution path exists.
- Global verification must distinguish sandbox permission failures from real install failures.
- Specs, docs, adapters, and tests must not contain API keys, local absolute contributor paths, or private browser state.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `read-web` MUST return `status: ok` for short but valid public pages that have clear visible title/body content.
- **FR-002**: `read-web` MUST keep structured non-OK failures for unsafe, blocked, empty, login-walled, and insufficient-content pages.
- **FR-003**: The free-provider quality gate MUST distinguish "short but valid" pages from "insufficient content" pages using explicit evidence fields.
- **FR-004**: The Parallel paid fallback MUST use the currently supported Parallel Extract API shape, authentication header, URL, and response parser.
- **FR-005**: Paid fallback MUST execute only when the user opts in and MUST respect configured cost caps and ledger recording.
- **FR-006**: The Reddit provider MUST support dict-style subreddit/listing JSON responses with `data.children`.
- **FR-007**: The Reddit provider MUST preserve existing list-style post/comment extraction behavior.
- **FR-008**: `read-user-tabs` CDP unreachable errors MUST include a user-facing setup path or pointer that can be followed without reading internal specs.
- **FR-009**: README, CLI help/schema, and installed adapter text MUST include safe loopback CDP setup and verification guidance for `read-user-tabs`.
- **FR-010**: `read-user-tabs` MUST continue requiring explicit approval scopes before reading content or screenshots.
- **FR-011**: Interactive provider discovery MUST truthfully represent which providers are implemented and which are unavailable/pending.
- **FR-012**: Browser Use Cloud interactive execution MUST have a live smoke path with credentials, cost evidence, and stable structured output.
- **FR-013**: Browserbase and local interactive providers MUST either be implemented end-to-end or removed/marked unavailable from user-facing daily-use surfaces.
- **FR-014**: Global install verification MUST detect stale pipx/global shims by comparing CLI/schema behavior against expected branch defaults.
- **FR-015**: Agent adapters MUST remain thin and MUST call the shared CLI rather than embedding provider logic.
- **FR-016**: Tests MUST follow TDD order for each bug fix: failing behavior test first, minimal implementation second, green verification third.
- **FR-017**: The full repository test suite MUST pass before implementation completion.
- **FR-018**: No generated docs, adapters, tests, or issues may expose secrets or contributor-local absolute paths.

### Key Entities *(include if feature involves data)*

- **FetchResult**: Public read result envelope including status, provider, content, quality evidence, cost evidence, and structured error metadata.
- **QualityAssessment**: Evidence object that distinguishes short-valid content, insufficient content, blocked signals, login walls, and provider quality failures.
- **ParallelExtractRequest**: Paid fallback request contract covering target URLs, extraction objective, authentication, response parsing, cost, and provider errors.
- **RedditListing**: Normalized representation of subreddit/listing entries derived from dict-style Reddit JSON.
- **CdpSetupGuide**: User-facing setup and verification instructions for a safe loopback CDP browser profile.
- **InteractiveProviderCapability**: Discovery record describing provider availability, credential requirements, approval behavior, cost controls, and whether execution is live or unavailable.
- **GlobalInstallState**: Verification record for the global shim path, venv target, schema defaults, adapter files, and install health.
- **VerificationMatrix**: Maintainer checklist connecting each skill surface to required CLI, test, live smoke, and documentation evidence.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `browser-fetch-router test-acceptance --include-network --json` reports all expected public success cases as `ok`, including `example-read-network`.
- **SC-002**: With a real Parallel API key and paid opt-in, a live fallback smoke against a known public page returns structured extracted content and records cost evidence.
- **SC-003**: `read-web` CLI tests cover short-valid pages, blocked/empty pages, Parallel success/error paths, and Reddit listing/post paths.
- **SC-004**: A user following only README/help/adapter guidance can start a temporary loopback Chrome CDP profile and pass `read-user-tabs list`, `read active`, and `screenshot active`.
- **SC-005**: Interactive provider schema/help/docs no longer advertise non-live providers as daily-use ready; every advertised live provider has a passing CLI smoke.
- **SC-006**: Global install verification detects stale global state before reinstall and passes after reinstall from the reviewed branch.
- **SC-007**: `python3 -m pytest tests/browser_fetch_router` passes with all new regression tests.
- **SC-008**: A tracked-file hardcoded-path and secret-pattern sweep reports no contributor paths or credential values.

## Assumptions

- Target users are agents and maintainers using the installed CLI from outside the source repository.
- Live vendor verification may use credentials already stored in the user's password manager, but secrets must remain outside specs, code, docs, logs, and issues.
- CDP setup should use an isolated temporary browser profile and loopback-only remote debugging; normal browser profiles are not required.
- This feature may classify a provider as unavailable/pending instead of implementing it when no safe end-to-end execution path exists.
- Implementation remains scoped to reliability, discovery truthfulness, docs, adapters, tests, and verification for existing skill surfaces; new unrelated browser providers are out of scope.
