# Tasks: Install-Agent Open-Source Readiness

**Input**: Design documents from `specs/002-install-agent-readiness/`  
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/install-agent-cli.md, quickstart.md

**Tests**: Required. Constitution and feature spec require TDD; every behavior task below starts with a failing test.

**Organization**: Tasks are grouped by user story so each story can be implemented and verified independently.

## Phase 1: Setup

**Purpose**: Confirm current planning gate and scope before implementation.

- [x] T001 Record the five plan-review approvals and non-blocking carry-forward notes in specs/002-install-agent-readiness/root-cause.md
- [x] T002 Inspect current install-agent CLI/schema touchpoints in browser_fetch_router/install_agent.py, browser_fetch_router/cli.py, browser_fetch_router/schema.py, and browser_fetch_router/schemas/v1.json

---

## Phase 2: Foundational

**Purpose**: Shared contract scaffolding that blocks all user stories.

- [x] T003 [P] Add install-agent support matrix documentation shell in docs/browser-fetch-router-install-agent-contract.md
- [x] T004 [P] Add README pointer to install-agent contract and contributor verification flow in README.md
- [x] T005 Define stable result statuses, warning fields, skip reasons, and agent ordering in specs/002-install-agent-readiness/contracts/install-agent-cli.md

**Checkpoint**: Foundation ready; user story implementation can begin.

---

## Phase 3: User Story 1 - Reliable Default Agent Install (Priority: P1) MVP

**Goal**: Default multi-agent install is reliable: default agents install, Kimi is visible as expected non-fatal skipped/default-disabled, and unexpected default-root failures remain actionable.

**Independent Test**: In a controlled temp HOME with verified default roots and no Kimi root, `install-agent --all --json` returns aggregate `ok`, writes default agents, reports Kimi skipped, and preserves nonzero failure for unexpected default-root failures.

### Tests for User Story 1

- [x] T006 [P] [US1] Add failing Python test for default agent set, stable `--all` result order, and visible Kimi skipped/default-disabled entry in tests/browser_fetch_router/test_install_agent.py
- [x] T007 [P] [US1] Add failing Python test for `--all` aggregate `ok` with expected skips and aggregate `tool_setup_failed` for unexpected missing default roots in tests/browser_fetch_router/test_install_agent.py
- [x] T008 [P] [US1] Add failing CLI JSON/exit-code test for `install-agent --all --json` controlled HOME behavior in tests/browser_fetch_router/test_install_agent.py

### Implementation for User Story 1

- [x] T009 [US1] Add table-driven `AgentInstallContract` policy with supported/default distinctions in browser_fetch_router/install_agent.py
- [x] T010 [US1] Update `install_agents` aggregation to emit `ok`, `skipped`, warnings, skip reasons, artifacts, and failure semantics in browser_fetch_router/install_agent.py
- [x] T011 [US1] Update install-agent CLI/schema metadata for default-vs-supported behavior in browser_fetch_router/schema.py and browser_fetch_router/schemas/v1.json
- [x] T012 [US1] Update existing tests that lock old `AGENTS`/all-writes-every-destination behavior in tests/browser_fetch_router/test_install_agent.py
- [x] T013 [US1] Run `python3 -m pytest tests/browser_fetch_router/test_install_agent.py` and confirm US1 tests pass

**Checkpoint**: User Story 1 complete and independently testable.

---

## Phase 4: User Story 2 - Explicit Pi And Kimi Installs (Priority: P1)

**Goal**: Explicit Pi and Kimi installs follow documented discovery contracts while preserving env override semantics.

**Independent Test**: Explicit Pi writes to `~/.pi/agent/skills`; explicit Kimi writes only on opt-in and returns inheritance warning metadata; env overrides for Codex, Gemini, OpenCode, Pi, and Kimi still write under the override root containing `skills/`.

### Tests for User Story 2

- [x] T014 [P] [US2] Add failing Python test for Pi default destination `~/.pi/agent/skills/browser-fetch-router/SKILL.md` in tests/browser_fetch_router/test_install_agent.py
- [x] T015 [P] [US2] Add failing CLI test for explicit `install-agent pi --json` controlled HOME behavior in tests/browser_fetch_router/test_install_agent.py
- [x] T016 [P] [US2] Add failing Python test for explicit Kimi warning metadata in single-agent and `--select kimi` flows in tests/browser_fetch_router/test_install_agent.py
- [x] T017 [P] [US2] Add failing CLI test pinning explicit Kimi behavior when `.kimi/skills` is absent but the user explicitly opts in in tests/browser_fetch_router/test_install_agent.py
- [x] T018 [P] [US2] Add failing env override regression test for CODEX_HOME, GEMINI_HOME, OPENCODE_HOME, PI_HOME, and KIMI_HOME in tests/browser_fetch_router/test_install_agent.py
- [x] T019 [P] [US2] Add failing tests for `--force` overwrite behavior in explicit and selected install modes in tests/browser_fetch_router/test_install_agent.py

### Implementation for User Story 2

- [x] T020 [US2] Change Pi built-in root to `~/.pi/agent` while preserving `PI_HOME` root-containing-skills semantics in browser_fetch_router/install_agent.py
- [x] T021 [US2] Implement Kimi explicit warning metadata and explicit-opt-in path creation rules in browser_fetch_router/install_agent.py
- [x] T022 [US2] Preserve env override behavior for Codex, Gemini, OpenCode, Pi, and Kimi through the contract table in browser_fetch_router/install_agent.py
- [x] T023 [US2] Preserve explicit `--force` overwrite behavior across single-agent and selected installs in browser_fetch_router/install_agent.py and browser_fetch_router/cli.py
- [x] T024 [US2] Run `python3 -m pytest tests/browser_fetch_router/test_install_agent.py` and confirm US2 tests pass

**Checkpoint**: User Stories 1 and 2 complete and independently testable.

---

## Phase 5: User Story 3 - Contributor Hygiene And Docs (Priority: P2)

**Goal**: Contributor docs and verification prove open-source readiness without committable generated artifacts or tracked machine-local paths.

**Independent Test**: Documented contributor verification leaves `git status --short` empty, package installability works outside the repo, and tracked-file sweep finds no contributor-local absolute paths.

### Tests for User Story 3

- [x] T025 [P] [US3] Add CI-friendly tracked-path sweep test that avoids embedding contributor-local absolute paths in tests/browser_fetch_router/test_install_agent.py
- [x] T026 [P] [US3] Add failing test or static assertion that install-agent docs mention Pi migration and Kimi inheritance caveat in tests/browser_fetch_router/test_install_agent.py
- [x] T027 [P] [US3] Add failing docs/schema assertion for support matrix fields and `--adapter-path` filename rejection guidance in tests/browser_fetch_router/test_install_agent.py

### Implementation for User Story 3

- [x] T028 [US3] Complete support matrix, status semantics, adapter-path edge behavior, Pi migration note, and source evidence in docs/browser-fetch-router-install-agent-contract.md
- [x] T029 [US3] Update README install/test/contributor flow and install-agent docs pointer in README.md
- [x] T030 [US3] Update install-agent schema/help descriptions to mention supported/default distinction and selected agent semantics in browser_fetch_router/schema.py and browser_fetch_router/schemas/v1.json
- [x] T031 [US3] Run `python3 -m pytest tests/browser_fetch_router/test_install_agent.py` and confirm US3 tests pass

**Checkpoint**: All user stories complete and independently testable.

---

## Phase 6: Polish & Cross-Cutting Verification

**Purpose**: Cleanup, final verification, live smoke, and review gates.

- [x] T032 Run AI slop cleanup on touched files: browser_fetch_router/install_agent.py, browser_fetch_router/cli.py, browser_fetch_router/schema.py, browser_fetch_router/schemas/v1.json, tests/browser_fetch_router/test_install_agent.py, README.md, docs/browser-fetch-router-install-agent-contract.md
- [x] T033 Run `python3 -m pytest tests/browser_fetch_router/test_install_agent.py`
- [x] T034 Run `python3 -m pytest tests/browser_fetch_router`
- [x] T035 Run `git diff --check`
- [x] T036 Run outside-repo `pip install .` and `browser-fetch-router --help` from a temp directory
- [x] T037 Run live smoke for `browser-fetch-router install-agent --help`, `--all --json`, explicit Pi, explicit Kimi, and env override selection from installed package
- [x] T038 Run documented contributor install/test flow and verify `git status --short` stays empty
- [x] T039 Run tracked-file hardcoded-path sweep and record zero matches without committing contributor-local absolute paths
- [x] T040 Update PR evidence notes in specs/002-install-agent-readiness/root-cause.md with exact commands, exit codes, stdout/stderr summaries, temp dirs abstracted, and artifacts created
- [ ] T041 Get post-implementation reviews from Claude, Gemini, Grok, GLM, and DeepSeek; resolve all valid findings before PR readiness

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies.
- **Foundational (Phase 2)**: depends on Setup completion.
- **US1 (Phase 3)**: depends on Foundational; MVP.
- **US2 (Phase 4)**: can start after Foundational but should land after US1 policy table to avoid duplicate install aggregation changes.
- **US3 (Phase 5)**: can start after Foundational but final docs depend on US1/US2 behavior.
- **Polish (Phase 6)**: depends on all implemented user stories.

### User Story Dependencies

- **US1**: no story dependency; establishes shared policy and aggregate semantics.
- **US2**: depends on US1's policy table and result model.
- **US3**: depends on US1/US2 decisions for accurate docs and verification.

### Parallel Opportunities

- T003 and T004 can run in parallel.
- T006, T007, and T008 can be written in parallel before US1 implementation.
- T014 through T019 can be written in parallel before US2 implementation.
- T025 through T027 can be written in parallel before US3 docs/schema work.
- Final verification tasks T033 through T039 can run independently after cleanup, except installed-package smokes require package install first.

## Parallel Example: User Story 2

```text
Task: "Add failing Python test for Pi default destination in tests/browser_fetch_router/test_install_agent.py"
Task: "Add failing Python test for explicit Kimi warning metadata in tests/browser_fetch_router/test_install_agent.py"
Task: "Add failing env override regression test for CODEX_HOME, GEMINI_HOME, OPENCODE_HOME, PI_HOME, and KIMI_HOME in tests/browser_fetch_router/test_install_agent.py"
```

## Implementation Strategy

1. Complete Setup and Foundational docs shell.
2. Deliver US1 first as MVP: default `--all` reliability and aggregate result semantics.
3. Deliver US2: Pi/Kimi explicit behavior and env overrides.
4. Deliver US3: docs, contributor hygiene, package readiness evidence.
5. Run polish verification and required post-implementation external reviews before PR readiness.

## Notes

- Every behavior test must be observed failing before implementation.
- Keep policy table as the single implementation path; do not add parallel fallback directories.
- Do not duplicate install policy inside adapter templates or plugin manifests.
- Do not merge or mark PR ready until all five post-implementation reviewers approve.
