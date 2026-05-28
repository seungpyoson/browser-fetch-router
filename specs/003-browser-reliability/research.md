# Research: Browser Fetch Router Daily-Use Reliability

## Decision: Treat `example.com` as short-valid public content, not insufficient content

**Rationale**: Live investigation showed the free provider returns real page content for `https://example.com`, but `quality.py` currently requires at least 80 words and marks the page as insufficient. The built-in network acceptance case expects `ok`, so the quality gate is stricter than the product contract for short valid pages.

**Alternatives considered**:

- Lower the global minimum word count only: rejected because empty and blocked pages still need explicit evidence-driven rejection.
- Always allow `example.com`: rejected because that would hardcode one URL and violate single-path design.
- Add a short-valid classification with evidence: chosen because it generalizes to other concise public pages while preserving blocked/empty detection.

## Decision: Update Parallel fallback to the current Extract API contract

**Rationale**: The current adapter posts `{"url": url}` to `https://api.parallel.ai/v1beta/extract` with bearer auth and receives HTTP 422 with a real key. A direct documented request shape using `https://api.parallel.ai/v1/extract`, `x-api-key`, and `{"urls": [...], "objective": ...}` succeeds and returns extract results. This is API/request-shape drift, not credential failure.

**Alternatives considered**:

- Treat Parallel as unavailable: rejected because live direct API evidence proves the provider works.
- Keep both v1beta and v1 paths: rejected because it creates dual behavior unless there is a tested compatibility requirement.
- Move to the current v1 request and parser: chosen.

## Decision: Add dict-style Reddit listing shaping alongside existing post/comment shaping

**Rationale**: Reddit post/comment URLs work through list-shaped JSON. Subreddit listing URLs return dict-shaped JSON with `data.children`, and `_extract_title()` already recognizes that shape while `_shape_reddit_listing()` does not. This is a narrow provider shaper gap.

**Alternatives considered**:

- Route listings through generic Jina: rejected because Reddit is already a dedicated provider route and public JSON gives structured listing content.
- Add a separate Reddit listing provider module: rejected because the existing module owns Reddit JSON shaping.
- Extend `_shape_reddit_listing()` for dict listings: chosen.

## Decision: Make CDP setup user-facing instead of internal-spec-only

**Rationale**: `read-user-tabs` works when a loopback Chrome CDP endpoint is started with a temporary profile, including list/read/screenshot flows. Without CDP it returns a correct structured `cdp_unreachable` failure, but README/help/adapters do not provide the safe setup path; the command is only documented in an internal feature quickstart.

**Alternatives considered**:

- Auto-launch the user's normal browser profile: rejected because it risks authenticated state and profile mutation.
- Treat CDP absence as product failure: rejected because the CLI is correct when the required endpoint is absent.
- Document and surface a safe loopback temporary-profile setup path: chosen.

## Decision: Interactive provider discovery must distinguish live providers from stubs

**Rationale**: Browser Use Cloud has a live provider path with credentialed smoke evidence and cost ledger recording. Browserbase and local currently return `provider_unavailable` after credential/dependency checks, so exposing them as peer daily-use providers is misleading unless they are implemented.

**Alternatives considered**:

- Keep stubs advertised and rely on runtime error: rejected because agents treat schema/help/adapters as capability truth.
- Implement every provider immediately: allowed by spec, but only if it can be verified end to end with credentials/dependencies.
- Mark unavailable providers clearly until implemented: chosen as the minimum truthful path.

**Reviewer follow-up evidence**:

- Gemini review found a real reservation leak: Browser Use Cloud success without `total_cost_usd` returned `ok` while leaving the preflight cost reservation in the session ledger.
- Red TDD test `test_cloud_success_without_reported_cost_releases_reservation` reproduced the leak with `ledger.session_total("bfr-cloud-no-cost") == 0.25` after an `ok` provider result with no reported cost.
- The fix releases the reservation on the successful no-reported-cost branch, matching the existing failure no-reported-cost behavior.
- Gemini and DeepSeek review found a second real cost-control bug: `_reserve_hosted_cost()` passed `session_cap=current_session_total + amount` and `daily_cap=current_daily_total + amount`, making cumulative hosted-browser caps ineffective.
- Red TDD tests `test_cloud_session_cap_blocks_second_call_before_provider` and `test_cloud_daily_cap_blocks_cross_session_call_before_provider` reproduced the bypass: a second paid call still reached the provider and returned `ok` after prior spend.
- The fix applies the single public `--max-cost-usd` cap to request, session, and daily ledger dimensions until separate knobs exist, failing closed instead of silently allowing cumulative overrun.
- DeepSeek and Kimi review found a third real Browser Use Cloud control gap: `--max-steps` was accepted and passed through, but Browser Use Cloud v3 does not expose a create-session `maxSteps` field and the client did not enforce steps while polling.
- Red TDD test `test_browser_use_cloud_stops_running_session_when_step_cap_is_reached` reproduced the gap by returning `browser_use_cloud_empty_output` after an over-cap running session instead of stopping the provider session with a step-cap error.
- The fix enforces `--max-steps` by polling provider `stepCount`, calling the Browser Use Cloud stop endpoint for nonterminal sessions at the cap, and preserving any stop-response `totalCostUsd` so the ledger records billed spend on provider failure.
- Red TDD test `test_cloud_provider_exception_releases_reservation` reproduced an exception path that could escape while a preflight reservation was live; the fix releases the reservation and returns a structured `provider_unavailable/browser_use_cloud_exception` envelope.
- Claude final-branch review found two real edge cases: dict-style Reddit listings with no titled children returned `ok` with only `# Reddit listing`, and explicit hosted-browser caps below the default were silently raised to `0.25`.
- Red TDD tests `test_reddit_dict_empty_listing_reports_insufficient_content` and `test_cloud_respects_explicit_cost_cap_below_default` reproduced the gaps; the fixes preserve `reddit_empty_listing` structured failure and pass the caller's finite nonnegative `--max-cost-usd` value through unchanged.
- `python3 -m pytest tests/browser_fetch_router/test_browser_reliability_providers.py tests/browser_fetch_router/test_interactive.py -q` exited `0` with `37 passed`.
- `python3 -m pytest tests/browser_fetch_router/test_interactive.py tests/browser_fetch_router/test_browser_use_cloud.py tests/browser_fetch_router/test_cost.py tests/browser_fetch_router/test_cli_contract.py -q` exited `0` with `47 passed`.
- `python3 -m pytest tests/browser_fetch_router/test_browser_reliability_cli.py tests/browser_fetch_router/test_browser_reliability_providers.py tests/browser_fetch_router/test_quality.py tests/browser_fetch_router/test_read_web.py tests/browser_fetch_router/test_read_user_tabs.py tests/browser_fetch_router/test_interactive.py tests/browser_fetch_router/test_browser_use_cloud.py tests/browser_fetch_router/test_cost.py tests/browser_fetch_router/test_cli_contract.py tests/browser_fetch_router/test_install_agent.py -q` exited `0` with `159 passed`.

## Decision: Add explicit global install freshness verification

**Rationale**: Branch/temp-venv verification can pass while the actual global shim points to a stale pipx environment. Live smoke must prove the real `command -v browser-fetch-router` target, schema defaults, adapter files, and branch expectation agree.

**Alternatives considered**:

- Assume reinstall succeeded if `pip install .` passed: rejected because it does not verify the user's global command.
- Only verify `browser-fetch-router --help`: rejected because stale commands can still expose help.
- Verify shim target, schema defaults, doctor, adapters, and outside-repo execution: chosen.

## Decision: Keep credentials outside repo artifacts and issues

**Rationale**: Provider credentials are available through the user's password manager, but repo governance forbids secrets in docs, tests, logs, adapters, or GitHub issues. Live smokes may use environment variables and must redact values.

**Alternatives considered**:

- Store test keys in fixtures: rejected.
- Require live vendor tests in CI: rejected unless CI secret management is explicitly configured later.
- Keep live vendor tests as local gated verification with env vars: chosen.

## Evidence: current branch verification after reliability fixes

- `python3 -m pytest tests/browser_fetch_router -q` exited `0` with `748 passed` when run outside the macOS sandbox for the real-subprocess lifecycle test.
- `git diff --check` exited `0`.
- Tracked-file contributor-path sweep for local home path patterns found `0` matches.
- Secret-pattern sweep found no live secrets; the only match was an intentional fake audit fixture.
- Package installability passed from a temporary outside-repo virtualenv: `pip install -q .`, `browser-fetch-router --help`, and `browser-fetch-router schema --json` all exited `0`; schema reported `browser-fetch-router.v1` and `interactive-browser.providerCapabilities[0].status == live`.
- Branch `doctor --global-install --json` verifier first detected the stale
  global shim (`interactive-browser.--max-cost-usd` default `0.05`, missing
  provider capability statuses). After `pipx install --force .`, the same
  verifier passed from a temporary HOME with `status: ok`, cost default `0.25`,
  and cloud provider status `live`.
- Global controlled-HOME adapter smoke passed: `install-agent --all --force
  --json` returned `ok` with Kimi skipped/default-disabled by design, explicit
  `install-agent kimi --force --json` returned `ok`, and global `read-web
  https://example.com --json --no-cache` returned `ok` via `jina-reader`.
- Registry-backed current-package paid smoke exited `0` with `status: ok`, `provider: parallel`, and `content_markdown: Hello World!`.
- Live Reddit listing smoke exited `0` with `status: ok`, `provider: reddit-json`, provider URL `https://www.reddit.com/r/python.json?limit=3`, and non-empty listing content.
- Live `read-user-tabs` CDP smoke used a temporary Chrome profile on loopback `127.0.0.1:9222`; `/json/version`, `list`, `list --all` with approval, `read active`, and `screenshot active` all exited `0` with `status: ok`. The temporary Chrome instance was closed and port `9222` was no longer listening afterward.
- Registry cache currently has `PARALLEL_API_KEY` and `BROWSER_USE_API_KEY` present. Credentialed Browser Use Cloud live smoke exited `0` with `status: ok`, `provider: browser-use-cloud`, content containing `"Example Domain"`, `remote_status: stopped`, `step_count: 0`, and `total_cost_usd: 0.004498000000000000261901611509`. Unit/contract tests cover Browser Use Cloud success, auth failure, max-step stop, timeout, exception release, reported-cost recording, and cumulative caps.
- Latest TDD follow-up at `75ec0df` reclassified a missing global shim as a `command_mismatches` entry instead of `schema_mismatches`; the red public-CLI regression test failed before the one-line fix and passed afterward. Slice review approvals were obtained from Claude, Gemini, Kimi, DeepSeek, GLM, and Grok. Final whole-feature T046 remains pending until final post-implementation reviews are complete at the latest head.
- Reviewer follow-up TDD added Browser Use Cloud coverage for missing session IDs, bytes request bodies, and best-effort stop/evidence after poll transport failures and poll HTTP error responses. The red tests failed before the provider fixes and passed afterward. Spec evidence paths now use placeholders rather than contributor-machine absolute paths.

The #58 and #59 sections below are historical phase-local evidence captured at earlier branch states while the test suite was still growing. Their full-suite totals differ from the current `748 passed` count because later user-story and review-follow-up tests were added after those captures.

## Evidence: #58 read-web short-valid page reliability

**Baseline before fix**:

- `HOME=<tmp-home>/bfr-home-baseline python3 -m browser_fetch_router read-web https://example.com --json --no-cache` exited `3` with `status: quota_or_key_missing`, `error.code: paid_fallback_not_allowed`, and primary low-quality Jina content.
- `HOME=<tmp-home>/bfr-home-baseline python3 -m browser_fetch_router test-acceptance --include-network --json` exited `1`; `example-read-network` expected `ok` but returned `quota_or_key_missing`.
- First TDD test `test_jina_accepts_short_valid_public_page_content` failed with `insufficient_content != ok`.

**Green evidence after fix**:

- `python3 -m pytest tests/browser_fetch_router/test_quality.py -q` -> `16 passed`; coverage includes the documented 20-word/120-char short-valid boundary, below-boundary rejection, strong login prompts in visible HTML, JS challenge blocking, empty semantic pages, high-boilerplate rejection, script/style stripping, and `sign in` not matching inside words.
- `python3 -m pytest tests/browser_fetch_router/test_browser_reliability_cli.py tests/browser_fetch_router/test_browser_reliability_providers.py tests/browser_fetch_router/test_quality.py tests/browser_fetch_router/test_read_web.py -q` -> `32 passed`.
- `HOME=<tmp-home>/bfr-home-us1 python3 -m browser_fetch_router read-web https://example.com --json --no-cache` exited `0` with `status: ok`, `provider: jina-reader`, `quality.is_short_valid_content: true`, `quality.passes_quality_gate: true`, and `word_count: 58`.
- `HOME=<tmp-home>/bfr-home-us1 python3 -m browser_fetch_router test-acceptance --include-network --json` exited `0`; `18` cases passed, `0` failed, and `example-read-network` returned `ok`.
- `git diff --check` exited `0`.
- `python3 -m pytest tests/browser_fetch_router -q` in the sandbox failed only at `test_Q_run_cleanup_real_subprocess_lands_in_cleaned_bucket` because macOS denied `psutil` process enumeration via `sysctl`.
- Escalated rerun of `python3 -m pytest tests/browser_fetch_router -q` exited `0` with `704 passed`.

## Evidence: #59 Parallel paid fallback contract

**Baseline before fix**:

- A credentialed request to the previous adapter contract used `https://api.parallel.ai/v1beta/extract`, bearer authentication, and `{"url": ...}`; Parallel returned HTTP `422` with validation-error details. This proved API/request-shape drift rather than an absent credential.
- With the corrected fallback-eligible URL but no key, `HOME=<tmp-home>/bfr-home-fallback-no-key python3 -m browser_fetch_router read-web https://raw.githubusercontent.com/octocat/Hello-World/master/README --allow-paid --json --no-cache --max-chars 500` exited `3` with `status: quota_or_key_missing`, `provider: parallel`, and `error.code: parallel_key_missing`, proving the URL reaches the paid fallback branch without exposing a key.

**Green evidence after fix**:

- Unit/provider tests assert `POST https://api.parallel.ai/v1/extract`, `x-api-key`, body `{"urls": [...], "objective": ...}`, full-content parsing, excerpt parsing, 4xx details, 429 details, missing-key short-circuit, malformed JSON, empty result, and URL-specific Extract errors.
- `python3 -m pytest tests/browser_fetch_router/test_browser_reliability_cli.py tests/browser_fetch_router/test_browser_reliability_providers.py tests/browser_fetch_router/test_read_web.py tests/browser_fetch_router/test_quality.py tests/browser_fetch_router/test_acceptance_contract.py -q` exited `0` with `51 passed`.
- Using an ephemeral environment variable for a newly created test key, `HOME=<tmp-home>/bfr-paid-smoke-home python3 -m browser_fetch_router read-web https://raw.githubusercontent.com/octocat/Hello-World/master/README --allow-paid --json --no-cache --max-chars 500` exited `0` with `status: ok`, `provider: parallel`, and non-empty content.
- In the same ephemeral key session, `HOME=<tmp-home>/bfr-paid-smoke-home python3 -m browser_fetch_router test-acceptance --include-network --include-paid --json` exited `0`; `19` cases passed, `0` failed, and `parallel-paid-extract` returned `ok`.
- An exact-secret worktree scan using the key supplied only through stdin found `0` matches.
- `git diff --check` exited `0`.

**Reviewer follow-up evidence**:

- A TDD regression test proved the normal `read_web(..., allow_paid=True)` path was still handing Parallel the generic primary `SafeHttpClient` instead of Parallel's provider-specific 90s client; the red result was `provider_unavailable` when the generic primary client was guarded against paid fallback use.
- The fix preserves explicitly injected test/client behavior, but removes only the internally-created primary client before dispatching paid fallback so `parallel.fetch()` constructs its own 90s client.
- Added coverage for registry-relevant auth failure mapping (`401` -> `quota_or_key_missing` / `parallel_auth_failed`) and normalized `full_content` trimming to match excerpt trimming.
- `python3 -m pytest tests/browser_fetch_router/test_browser_reliability_cli.py tests/browser_fetch_router/test_browser_reliability_providers.py tests/browser_fetch_router/test_read_web.py tests/browser_fetch_router/test_quality.py tests/browser_fetch_router/test_acceptance_contract.py -q` exited `0` with `54 passed`.
- `python3 -m pytest tests/browser_fetch_router -q` exited `0` with `719 passed` when rerun outside the macOS sandbox; the sandboxed run failed only at the known real-subprocess cleanup test because `psutil` process enumeration was denied.
- `git diff --check` exited `0`; tracked-file contributor-path sweep found `0` matches; secret-pattern sweep found only documented placeholders and the pre-existing fake audit fixture.
- Package installability passed from an external temporary directory outside the repository: `pip install -q .`, `browser-fetch-router --help`, and `browser-fetch-router schema --json` all exited `0`.
- Registry-backed current-package paid smoke exited `0` with `status: ok`, `provider: parallel`, and `content_markdown: Hello World!`.
- Registry-backed current-package paid acceptance exited `0`; `19` cases passed, `0` failed, and `parallel-paid-extract` returned `ok`.
