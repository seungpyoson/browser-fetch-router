# Implementation Plan: Browser Fetch Router Daily-Use Reliability

**Branch**: `003-browser-reliability` | **Date**: 2026-05-28 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `specs/003-browser-reliability/spec.md`

## Summary

Make all exposed Browser Fetch Router daily-use surfaces either work end to end or fail with an actionable, truthful contract. The work is scoped to five independently testable slices: public `read-web` reliability, Reddit listing support, `read-user-tabs` setup discoverability, interactive provider truthfulness/live execution, and global install verification. The implementation approach is test-first: capture each current symptom through public CLI/provider tests, fix the shared CLI/provider path, then update docs/adapters/schema so agents see the same contract as users.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: Python stdlib, `psutil`, `websockets`, `pytest`; no new runtime dependency may be declared unless imported by implementation in the same change
**Storage**: Local filesystem state under Browser Fetch Router cache/state/config dirs, cost SQLite ledger, installed adapter `SKILL.md` files
**Testing**: `pytest`, CLI subprocess tests, provider contract tests, optional live network/vendor smokes with credentials supplied through environment variables
**Target Platform**: macOS and Unix-like developer environments for CLI use; package remains portable Python
**Project Type**: Standalone Python CLI package with thin multi-agent adapter skill files
**Performance Goals**: Normal public reads and provider tests remain human-interactive latency; no repeated paid calls caused by cache/provider mismatch; global verification should finish in seconds outside live vendor waits
**Constraints**: Explicit user approval for browser state and hosted browser usage; no secrets in repo files/logs/issues; no contributor-local hardcoded paths; adapters remain thin; TDD required for every bug fix
**Scale/Scope**: Existing CLI surfaces only: `read-web`, `read-user-tabs`, `interactive-browser`, `schema`, `doctor`, `test-acceptance`, and `install-agent` verification

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- Shared CLI Ownership: PASS. Provider routing, quality gates, approvals, cost, schema, and install verification remain in `browser_fetch_router/`; adapter changes are documentation-only thin CLI guidance.
- Explicit Authorization And Secret Safety: PASS. Live vendor checks use environment variables only. Specs/contracts forbid secrets in files, logs, docs, adapter files, and issues. CDP instructions require loopback plus an isolated temporary profile.
- Test-First Reliability: PASS. Each user story includes failing behavior tests before implementation, with final `python3 -m pytest tests/browser_fetch_router`.
- Portable Installation Contracts: PASS. Global install verification inspects the real shim/venv/schema/adapters instead of guessed machine paths.
- Single Path System Design: PASS. Fixes target shared provider/CLI paths and remove or mark obsolete stub behavior rather than preserving parallel fake paths.
- Review And Release Workflow: PASS FOR PLANNING. External six-model review (Claude, Gemini, Kimi, DeepSeek, GLM, and Grok) is required before implementation execution; tasks include that as a foundational gate before code tasks begin.

## Project Structure

### Documentation (this feature)

```text
specs/003-browser-reliability/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── global-install-verification.md
│   ├── interactive-browser-cli.md
│   ├── read-user-tabs-cli.md
│   └── read-web-cli.md
└── tasks.md
```

### Source Code (repository root)

```text
browser_fetch_router/
├── acceptance.py
├── cli.py
├── interactive.py
├── quality.py
├── read_user_tabs.py
├── read_web.py
├── schema.py
├── adapters/
│   ├── claude/SKILL.md
│   ├── codex/SKILL.md
│   ├── gemini/SKILL.md
│   ├── kimi/SKILL.md
│   ├── opencode/SKILL.md
│   └── pi/SKILL.md
├── providers/
│   ├── browser_use_cloud.py
│   ├── jina.py
│   ├── parallel.py
│   └── reddit.py
└── schemas/
    └── v1.json

docs/
├── browser-fetch-router-approvals-contract.md
├── browser-fetch-router-install-agent-contract.md
└── browser-fetch-router-*-contract.md

tests/browser_fetch_router/
├── test_acceptance*.py
├── test_cli_contract.py
├── test_interactive.py
├── test_read_user_tabs.py
├── test_read_web*.py
└── test_*replication.py
```

**Structure Decision**: Use the existing single Python package layout. Add focused tests under `tests/browser_fetch_router/`; update existing provider modules, CLI/schema, README/docs, and adapter `SKILL.md` files in place. Do not add a second routing layer or duplicate provider policy in adapters.

## Phase 0: Research

Completed in [research.md](./research.md). All current symptoms have an identified implementation or product-contract cause with targeted evidence and no open clarification fields.

## Phase 1: Design And Contracts

Completed artifacts:

- [data-model.md](./data-model.md)
- [contracts/read-web-cli.md](./contracts/read-web-cli.md)
- [contracts/read-user-tabs-cli.md](./contracts/read-user-tabs-cli.md)
- [contracts/interactive-browser-cli.md](./contracts/interactive-browser-cli.md)
- [contracts/global-install-verification.md](./contracts/global-install-verification.md)
- [quickstart.md](./quickstart.md)

Agent context update: `AGENTS.md` now points its Spec Kit reference at `specs/003-browser-reliability/plan.md`.

## Post-Design Constitution Check

- Shared CLI Ownership: PASS. Contracts explicitly keep adapters thin and route all behavior through the shared CLI.
- Explicit Authorization And Secret Safety: PASS. Contracts require approval scopes for tab reads/screenshots and hosted-browser opt-in for paid providers.
- Test-First Reliability: PASS. Task generation must preserve failing-test-first order for every story.
- Portable Installation Contracts: PASS. Global install verification is a first-class contract and success criterion.
- Single Path System Design: PASS. Stubbed providers must become live or be marked unavailable consistently; old fake-ready paths are not preserved.
- Review And Release Workflow: CONDITIONAL. Task generation is being requested now under one epic; generated tasks must include a mandatory six-model plan-review gate before implementation work starts.

## Complexity Tracking

No constitution violations require architectural complexity. The only conditional item is the review workflow gate, tracked as a blocking implementation task rather than a new code path.
