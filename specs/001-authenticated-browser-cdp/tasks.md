# Tasks: Authenticated Browser CDP

**Input**: Design documents from `specs/001-authenticated-browser-cdp/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/read-user-tabs-cli.md`, `quickstart.md`
**Tests**: Required. The feature constitution, spec, and user workflow require TDD. Test tasks must be written first and observed failing before implementation tasks for the same phase.
**Scope**: Issue group #6/#7 only. Install-agent portability (#4/#5) remains excluded.

## Phase 1: Setup

**Purpose**: Prepare dependency metadata and test files without implementing CDP behavior.

- [x] T001 Add `websockets>=16,<17` runtime dependency in `pyproject.toml`
- [x] T002 [P] Create read/screenshot orchestration test module in `tests/browser_fetch_router/test_read_user_tabs.py`
- [x] T003 [P] Create interactive-browser capability test module in `tests/browser_fetch_router/test_interactive.py`

---

## Phase 2: Foundational

**Purpose**: Shared validation and error taxonomy required by text and screenshot CDP operations.

**Critical**: Complete this phase before any user story implementation.

- [x] T004 [P] Add failing WebSocket target validation tests for missing URL, embedded credentials, bad scheme, host mismatch, and port mismatch in `tests/browser_fetch_router/test_cdp.py`
- [x] T005 Add CDP protocol exception classes, sanitized error messages, and WebSocket target validation helper in `browser_fetch_router/cdp.py`
- [x] T006 Add shared CDP exception-to-envelope mapping helper for text and screenshot operations in `browser_fetch_router/read_user_tabs.py`
- [x] T007 Run the foundational focused tests from `tests/browser_fetch_router/test_cdp.py` and `tests/browser_fetch_router/test_read_user_tabs.py`

**Checkpoint**: Shared validation rejects unsafe or mismatched `webSocketDebuggerUrl` values before any WebSocket connection attempt.

---

## Phase 3: User Story 1 - Read Approved Authenticated Tab Text (Priority: P1) MVP

**Goal**: An approved authenticated tab can be read through CDP and returns visible text with isolated-world evidence.

**Independent Test**: Start from a tab list containing an approved page with a matching `webSocketDebuggerUrl`; `read_tab("active", approval_scope="exact:<url>")` returns `status: "ok"`, bounded `content_markdown`, and `evidence.cdp_isolated_world: true`.

### Tests for User Story 1

- [x] T008 [US1] Add failing CDP command-client test for `Page.enable`, `Page.getFrameTree`, `Page.createIsolatedWorld`, and `Runtime.evaluate` ordering in `tests/browser_fetch_router/test_cdp.py`
- [x] T009 [US1] Add failing approved `read_tab` success test including max-character truncation and no raw CDP response leakage in `tests/browser_fetch_router/test_read_user_tabs.py`

### Implementation for User Story 1

- [x] T010 [US1] Implement one synchronous CDP WebSocket command client using `websockets.sync.client.connect` in `browser_fetch_router/cdp.py`
- [x] T011 [US1] Implement `fetch_tab_text` with isolated-world text extraction in `browser_fetch_router/cdp.py`
- [x] T012 [US1] Wire `read_tab` to the implemented text extractor and preserve approval-before-read behavior in `browser_fetch_router/read_user_tabs.py`
- [x] T013 [US1] Run the User Story 1 focused tests in `tests/browser_fetch_router/test_cdp.py` and `tests/browser_fetch_router/test_read_user_tabs.py`

**Checkpoint**: User Story 1 is independently functional and testable.

---

## Phase 4: User Story 2 - Receive Accurate Browser Capability Failures (Priority: P2)

**Goal**: Browser capability failures are truthful, precise, and do not claim unavailable fallback behavior.

**Independent Test**: Exercise invalid WebSocket URL, unreachable CDP, protocol extraction failure, and unavailable interactive-browser provider paths; each returns the expected error code without leaking cookies, headers, profile paths, raw CDP payloads, or misleading dependency text.

### Tests for User Story 2

- [x] T014 [US2] Add failing `read_tab` negative-path tests for invalid WebSocket URL, WebSocket URL mismatch, unreachable WebSocket, and sanitized `Runtime.evaluate` failure in `tests/browser_fetch_router/test_read_user_tabs.py`
- [x] T015 [US2] Add failing `interactive-browser` tests for local, browserbase, and cloud unavailable-provider status after approval gates in `tests/browser_fetch_router/test_interactive.py`

### Implementation for User Story 2

- [x] T016 [US2] Implement precise text-read failure envelopes for validation, transport, and protocol errors in `browser_fetch_router/read_user_tabs.py`
- [x] T017 [US2] Update `run_interactive_browser` to report truthful provider-unavailable status without launching providers in `browser_fetch_router/interactive.py`
- [x] T018 [US2] Run the User Story 2 focused tests in `tests/browser_fetch_router/test_read_user_tabs.py` and `tests/browser_fetch_router/test_interactive.py`

**Checkpoint**: User Story 2 is independently functional and testable.

---

## Phase 5: User Story 3 - Use The Same CDP Capability For Approved Screenshots (Priority: P3)

**Goal**: Approved screenshots use the same CDP authorization and transport validation class as text reads.

**Independent Test**: With an approved reachable tab and a safe output path, `screenshot_tab` writes a PNG artifact only after authorization, rejects shared transport failures with precise error codes, and never captures before approval.

### Tests for User Story 3

- [x] T019 [US3] Add failing CDP screenshot command-client test for `Page.captureScreenshot` base64 decoding through the shared validated WebSocket path in `tests/browser_fetch_router/test_cdp.py`
- [x] T020 [US3] Add failing `screenshot_tab` orchestration tests for approval-before-capture, shared WebSocket validation failures, and atomic artifact output in `tests/browser_fetch_router/test_read_user_tabs.py`

### Implementation for User Story 3

- [x] T021 [US3] Implement `fetch_tab_screenshot` through the shared CDP WebSocket client in `browser_fetch_router/cdp.py`
- [x] T022 [US3] Wire `screenshot_tab` to shared CDP transport error mapping and preserve output validation before capture in `browser_fetch_router/read_user_tabs.py`
- [x] T023 [US3] Run the User Story 3 focused tests in `tests/browser_fetch_router/test_cdp.py` and `tests/browser_fetch_router/test_read_user_tabs.py`

**Checkpoint**: All three user stories are independently functional and testable.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Carry forward non-blocking reviewer notes and run release gates.

- [x] T024 [P] Clarify screenshot inherited transport failures, vanished-tab behavior, and no-change `read-user-tabs list` contract in `specs/001-authenticated-browser-cdp/contracts/read-user-tabs-cli.md`
- [x] T025 [P] Add Linux/Chromium launch alternative and final validation sequence to `specs/001-authenticated-browser-cdp/quickstart.md`
- [ ] T026 Run full repository feature verification with `python3 -m pytest tests/browser_fetch_router` against `tests/browser_fetch_router`
- [x] T027 Verify package installability with `pip install .` from outside the repository using `pyproject.toml`
- [x] T028 Verify installed CLI help with `browser-fetch-router --help` from outside the repository using `pyproject.toml`

Verification note: T026 was re-executed after review fixes. Result was `629 passed, 1 failed`; the
single failure is the pre-existing macOS `psutil` permission error in
`tests/browser_fetch_router/test_round3_replication.py::test_Q_run_cleanup_real_subprocess_lands_in_cleaned_bucket`.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies.
- **Foundational (Phase 2)**: Depends on Setup completion and blocks all user stories.
- **User Story 1 (Phase 3)**: Depends on Foundational completion. This is the MVP.
- **User Story 2 (Phase 4)**: Depends on Foundational completion and can be validated independently after its tests and implementation are complete.
- **User Story 3 (Phase 5)**: Depends on Foundational completion and shares the CDP client created for User Story 1.
- **Polish (Phase 6)**: Depends on the selected user stories being complete.

### User Story Dependencies

- **US1 (P1)**: Required first for MVP text extraction.
- **US2 (P2)**: Can start after Foundational, but should be validated after US1 to avoid duplicate error-mapping churn.
- **US3 (P3)**: Can start after Foundational and the shared CDP client from US1 exists.

### Within Each Story

- Write failing tests first.
- Observe the failing behavior.
- Implement only the code required for that story.
- Run focused tests for the story before moving to the next story.

## Parallel Opportunities

- T002 and T003 can run in parallel because they create separate test files.
- T004 can run in parallel with documentation review because it only edits `tests/browser_fetch_router/test_cdp.py`.
- T024 and T025 can run in parallel during polish because they edit separate documentation files.
- After Foundational, independent reviewers can inspect US2 and US3 test design while US1 implementation proceeds, but implementation should remain sequential in this working session.

## Implementation Strategy

### MVP First

1. Complete Setup and Foundational.
2. Complete US1 tests and implementation.
3. Stop and validate approved text extraction independently.

### Incremental Delivery

1. Add US1 text extraction.
2. Add US2 precise failure envelopes and interactive-provider truthfulness.
3. Add US3 screenshot support through the same CDP path.
4. Run full verification and package install/help gates.

### Review Carry-Forward

- Gemini: preserve explicit tests for WebSocket host mismatch, scheme mismatch, embedded credentials, and sanitized `Runtime.evaluate` failures.
- Claude: make screenshot shared transport failures, vanished-tab behavior, quickstart portability, and no-change `list` contract explicit.
- Grok, GLM, and DeepSeek: keep install-agent portability (#4/#5) excluded and treat generated Spec Kit scaffolding as setup only.
