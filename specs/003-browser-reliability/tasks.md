# Tasks: Browser Fetch Router Daily-Use Reliability

**Input**: Design documents from `specs/003-browser-reliability/`
**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/](./contracts/)
**Tests**: Required by the feature spec and project constitution. Every behavior fix starts with a failing public CLI or public Python interface test.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel with other tasks in the same phase when files do not overlap.
- **[Story]**: Maps to the user story in `spec.md`.
- Every task includes at least one exact file path.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish the reliability feature branch and test fixtures before story work.

- [ ] T001 Capture current failing symptom evidence for `read-web`, Reddit listings, `read-user-tabs`, interactive providers, and global install in `specs/003-browser-reliability/research.md`
- [ ] T002 [P] Add reusable CLI subprocess helpers for reliability tests in `tests/browser_fetch_router/test_browser_reliability_cli.py`
- [ ] T003 [P] Add provider fixture helpers for short pages, Parallel responses, and Reddit listing JSON in `tests/browser_fetch_router/test_browser_reliability_providers.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Complete review and shared contract checks before any implementation story starts.

**Critical**: No user story implementation starts until this phase is complete.

- [ ] T004 Obtain and record Claude, Gemini, Kimi, DeepSeek, GLM, and Grok approvals for `specs/003-browser-reliability/plan.md` in `specs/003-browser-reliability/research.md`
- [ ] T005 [P] Add a schema/help contract test that verifies surface availability text for `read-web`, `read-user-tabs`, and `interactive-browser` in `tests/browser_fetch_router/test_cli_contract.py`
- [ ] T006 [P] Add a docs/adapters sweep test for CDP setup guidance, provider truthfulness, and no embedded secrets in `tests/browser_fetch_router/test_install_agent.py`

**Checkpoint**: Foundation ready. User story implementation can proceed with TDD.

---

## Phase 3: User Story 1 - Public read-web succeeds for valid public pages (Priority: P1) MVP

**Goal**: Valid public pages, including short valid pages, return useful content or precise structured failures.

**Independent Test**: `browser-fetch-router read-web https://example.com --json --no-cache` returns `status: ok`, and `browser-fetch-router test-acceptance --include-network --json` passes.

### Tests for User Story 1

- [x] T007 [P] [US1] Add a failing quality-gate test for short valid public page content in `tests/browser_fetch_router/test_browser_reliability_providers.py`
- [x] T008 [P] [US1] Add a failing CLI regression test for `read-web https://example.com --json --no-cache` in `tests/browser_fetch_router/test_browser_reliability_cli.py`
- [x] T009 [P] [US1] Add failing Parallel v1 Extract success and error-shape tests in `tests/browser_fetch_router/test_browser_reliability_providers.py`
- [x] T010 [P] [US1] Add a failing acceptance test that `example-read-network` passes under `--include-network` in `tests/browser_fetch_router/test_browser_reliability_cli.py`

### Implementation for User Story 1

- [x] T011 [US1] Implement short-valid content classification and evidence in `browser_fetch_router/quality.py`
- [x] T012 [US1] Preserve blocked/login/captcha/empty structured failures while applying the new quality evidence in `browser_fetch_router/quality.py`
- [x] T013 [US1] Update the Parallel adapter to the current Extract API URL, auth header, request body, and response parser in `browser_fetch_router/providers/parallel.py`
- [x] T014 [US1] Update paid fallback and acceptance case expectations for the corrected public read path in `browser_fetch_router/read_web.py` and `browser_fetch_router/acceptance.py`
- [x] T015 [US1] Document the `read-web` short-valid and paid fallback contract in `docs/browser-fetch-router-read-web-contract.md`

**Checkpoint**: User Story 1 is independently testable and provides the MVP.

---

## Phase 4: User Story 2 - Reddit routes handle listings and posts separately (Priority: P1)

**Goal**: Reddit subreddit/listing URLs and post/comment URLs both work through the Reddit provider.

**Independent Test**: `browser-fetch-router read-web https://www.reddit.com/r/python/ --json --no-cache` returns `status: ok`, while an existing Reddit post/comment URL remains `status: ok`.

### Tests for User Story 2

- [ ] T016 [P] [US2] Add a failing unit test for dict-style subreddit listing JSON shaping in `tests/browser_fetch_router/test_browser_reliability_providers.py`
- [ ] T017 [P] [US2] Add a CLI regression test for a subreddit listing URL and an existing post/comment URL in `tests/browser_fetch_router/test_browser_reliability_cli.py`

### Implementation for User Story 2

- [ ] T018 [US2] Extend Reddit listing shaping for dict-style `data.children` responses in `browser_fetch_router/providers/reddit.py`
- [ ] T019 [US2] Preserve empty-listing structured failures and post/comment shaping in `browser_fetch_router/providers/reddit.py`
- [ ] T020 [US2] Add Reddit listing coverage to public network acceptance or documented smoke cases in `browser_fetch_router/acceptance.py`

**Checkpoint**: User Story 2 is independently testable without changing generic `read-web` routing.

---

## Phase 5: User Story 3 - User-tab reading has a no-mistakes setup path (Priority: P2)

**Goal**: `read-user-tabs` remains approval-safe and gives users a complete loopback CDP setup path when no endpoint is available.

**Independent Test**: From no CDP listener, the CLI returns `cdp_unreachable` with setup guidance; after starting the documented temporary Chrome profile, list/read/screenshot commands return `status: ok`.

### Tests for User Story 3

- [ ] T021 [P] [US3] Add a failing test for `cdp_unreachable` setup guidance in `tests/browser_fetch_router/test_read_user_tabs.py`
- [ ] T022 [P] [US3] Add failing adapter/docs contract assertions for loopback CDP setup guidance in `tests/browser_fetch_router/test_install_agent.py`
- [ ] T023 [P] [US3] Add a failing schema/help contract assertion for CDP setup discoverability in `tests/browser_fetch_router/test_cli_contract.py`

### Implementation for User Story 3

- [ ] T024 [US3] Add safe loopback CDP setup hints to unreachable-CDP envelopes in `browser_fetch_router/read_user_tabs.py`
- [ ] T025 [US3] Add CDP setup guidance to CLI help/schema in `browser_fetch_router/cli.py`, `browser_fetch_router/schema.py`, and `browser_fetch_router/schemas/v1.json`
- [ ] T026 [US3] Add safe loopback CDP setup guidance to `README.md` and all adapter files under `browser_fetch_router/adapters/`
- [ ] T027 [US3] Validate the documented temporary Chrome CDP flow and record the commands in `specs/003-browser-reliability/quickstart.md`

**Checkpoint**: User Story 3 is independently testable with a temporary browser profile.

---

## Phase 6: User Story 4 - Interactive browser providers are truthful and live (Priority: P2)

**Goal**: `interactive-browser` only advertises providers as daily-use ready when they have a real end-to-end execution path.

**Independent Test**: Browser Use Cloud live smoke returns `status: ok`; Browserbase/local either pass real smokes or are consistently marked unavailable/pending in schema, help, docs, and adapters.

### Tests for User Story 4

- [ ] T028 [P] [US4] Add failing provider capability schema tests for live versus unavailable providers in `tests/browser_fetch_router/test_interactive.py`
- [ ] T029 [P] [US4] Add failing Browser Use Cloud success/error contract tests in `tests/browser_fetch_router/test_interactive.py`
- [ ] T030 [P] [US4] Add failing docs/adapters assertions for provider truthfulness in `tests/browser_fetch_router/test_install_agent.py`

### Implementation for User Story 4

- [ ] T031 [US4] Add or consolidate interactive provider capability metadata in `browser_fetch_router/interactive.py`
- [ ] T032 [US4] Ensure Browser Use Cloud execution, cost cap handling, and cost ledger recording are covered in `browser_fetch_router/interactive.py` and `browser_fetch_router/providers/browser_use_cloud.py`
- [ ] T033 [US4] Mark Browserbase and local providers as live only if implemented, otherwise unavailable/pending, in `browser_fetch_router/interactive.py`
- [ ] T034 [US4] Reflect provider capability truth in `browser_fetch_router/cli.py`, `browser_fetch_router/schema.py`, and `browser_fetch_router/schemas/v1.json`
- [ ] T035 [US4] Update interactive provider guidance in `README.md`, `docs/browser-fetch-router-interactive-browser-contract.md`, and all adapter files under `browser_fetch_router/adapters/`
- [ ] T036 [US4] Run a credentialed Browser Use Cloud smoke and record redacted evidence in `specs/003-browser-reliability/quickstart.md`

**Checkpoint**: User Story 4 is independently testable through schema/help and provider-specific CLI smokes.

---

## Phase 7: User Story 5 - Global install verification proves the real user CLI is current (Priority: P3)

**Goal**: Maintainers can prove the actual global command and installed adapters match the reviewed branch.

**Independent Test**: From outside the repository, global verification reports the shim target, expected schema defaults, doctor health, adapter files, and a public `read-web` smoke.

### Tests for User Story 5

- [ ] T037 [P] [US5] Add failing stale-global-install detection tests in `tests/browser_fetch_router/test_install_agent.py`
- [ ] T038 [P] [US5] Add failing outside-repo package installability and schema-default verification tests in `tests/browser_fetch_router/test_cli_contract.py`

### Implementation for User Story 5

- [ ] T039 [US5] Add global install freshness verification support to `browser_fetch_router/doctor.py` or `browser_fetch_router/install_agent.py`
- [ ] T040 [US5] Document global reinstall and freshness verification in `README.md` and `docs/browser-fetch-router-install-agent-contract.md`
- [ ] T041 [US5] Validate `pip install .` and `browser-fetch-router --help` from outside the repository in `specs/003-browser-reliability/quickstart.md`
- [ ] T042 [US5] Validate global adapter installation for default agents plus explicit Kimi in `specs/003-browser-reliability/quickstart.md`

**Checkpoint**: User Story 5 is independently testable from the real global shim.

---

## Phase 8: Polish & Cross-Cutting Verification

**Purpose**: Final checks across all stories.

- [ ] T043 [P] Run `python3 -m pytest tests/browser_fetch_router` and record the result in `specs/003-browser-reliability/quickstart.md`
- [ ] T044 [P] Run `git diff --check` and tracked-file path/secret sweeps, then record results in `specs/003-browser-reliability/quickstart.md`
- [ ] T045 [P] Verify package installability with a temporary virtualenv from outside the repository and record results in `specs/003-browser-reliability/quickstart.md`
- [ ] T046 Obtain post-implementation approvals from Claude, Gemini, Kimi, DeepSeek, GLM, and Grok and record verdicts in `specs/003-browser-reliability/research.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Setup completion and blocks all user story implementation.
- **User Stories (Phase 3+)**: Depend on Foundational completion.
- **Polish (Phase 8)**: Depends on all implemented stories selected for the PR.

### User Story Dependencies

- **US1 (P1)**: Starts after Phase 2. MVP and public web baseline.
- **US2 (P1)**: Starts after Phase 2. Independent Reddit provider slice.
- **US3 (P2)**: Starts after Phase 2. Independent CDP setup and docs slice.
- **US4 (P2)**: Starts after Phase 2. Independent interactive provider truthfulness slice.
- **US5 (P3)**: Starts after Phase 2. Independent global install verification slice.

### TDD Order

- Tests in each story must be written and observed failing before corresponding implementation tasks begin.
- Implementation tasks must turn only their story tests green before broad refactor.
- Story checkpoint commands must pass before moving to lower-priority stories in a sequential implementation.

## Parallel Opportunities

- T002 and T003 can run in parallel.
- T005 and T006 can run in parallel after T004 approval evidence is in progress.
- Test tasks within each user story can run in parallel because they touch different behavior surfaces or fixtures.
- US1, US2, US3, US4, and US5 can be staffed in parallel after Phase 2 if file conflicts are coordinated.
- Cross-cutting verification tasks T043, T044, and T045 can run in parallel after implementation.

## Parallel Example: User Story 1

```text
Task T007: Add a failing quality-gate test in tests/browser_fetch_router/test_browser_reliability_providers.py
Task T008: Add a failing CLI regression test in tests/browser_fetch_router/test_browser_reliability_cli.py
Task T009: Add failing Parallel v1 Extract tests in tests/browser_fetch_router/test_browser_reliability_providers.py
Task T010: Add failing acceptance coverage in tests/browser_fetch_router/test_browser_reliability_cli.py
```

## Implementation Strategy

### MVP First

1. Complete Phase 1 and Phase 2.
2. Complete US1 only.
3. Run the US1 independent test commands.
4. Stop and review before expanding scope.

### Incremental Delivery

1. US1 fixes public `read-web`.
2. US2 fixes Reddit-specific routing.
3. US3 makes `read-user-tabs` setup actionable.
4. US4 makes interactive providers truthful/live.
5. US5 proves the real global install.

### Issue Strategy

Create one epic issue for this feature. Each task issue should link back to that epic and include its task ID, phase, dependencies, target file paths, and independent verification command.
