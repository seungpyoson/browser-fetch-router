# Implementation Plan: Authenticated Browser CDP Access

**Branch**: `001-authenticated-browser-cdp` | **Date**: 2026-05-27 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `specs/001-authenticated-browser-cdp/spec.md`

## Summary

Implement the first working authenticated-browser path in Browser Fetch Router:
`read-user-tabs read` extracts approved visible tab text through Chrome DevTools
Protocol (CDP), `read-user-tabs screenshot` shares the same CDP transport and
authorization boundary, and `interactive-browser` reports unavailable providers
truthfully instead of implying a working fallback. The implementation replaces
the current unconditional CDP stubs with one validated WebSocket command client
and removes misleading dependency-only errors.

## Technical Context

**Language/Version**: Python 3.11+ package (`pyproject.toml` requires `>=3.11`)  
**Primary Dependencies**: Existing `psutil`; add `websockets>=16,<17` in the same
change that imports `websockets.sync.client.connect`  
**Storage**: Existing approval/session/audit files only; no new persistent store  
**Testing**: `pytest`; behavior-first tests through public Python functions and
CLI dispatch where possible  
**Target Platform**: macOS/Linux CLI with Chrome/Chromium DevTools endpoint;
Windows-compatible Python code where filesystem behavior is existing-safe  
**Project Type**: Standalone Python CLI/package  
**Performance Goals**: One tab read completes within the existing CLI timeout
expectations for local DevTools; no unbounded CDP message reads  
**Constraints**: Preserve approval/default-deny checks; do not expose cookies or
credentials; validate CDP WebSocket URLs against the already-validated CDP base;
do not implement parallel fallback browser paths  
**Scale/Scope**: One consolidated issue group (#6/#7); install-agent portability
(#4/#5) remains out of scope

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **Shared CLI Ownership**: PASS. All provider/CDP behavior remains in
  `browser_fetch_router` CLI modules; adapters and skills are untouched.
- **Explicit Authorization And Secret Safety**: PASS. Reads and screenshots keep
  `_resolve_and_authorize_tab`; WebSocket target validation is planned before
  any CDP command is sent.
- **Test-First Reliability**: PASS. Tasks must start with failing tests for
  text extraction, screenshot behavior, error taxonomy, and dependency metadata.
- **Portable Installation Contracts**: PASS. A runtime dependency is declared
  only because the implementation imports it; package installability stays a
  release gate.
- **Single Path System Design**: PASS. One CDP WebSocket command path replaces
  stubs. `interactive-browser` status is made truthful rather than adding a
  second browser automation path.

## Project Structure

### Documentation (this feature)

```text
specs/001-authenticated-browser-cdp/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── read-user-tabs-cli.md
└── checklists/
    └── requirements.md
```

### Source Code (repository root)

```text
browser_fetch_router/
├── cdp.py                 # CDP base validation, tab list fetch, new WebSocket command client
├── read_user_tabs.py      # Authorization-preserving read/screenshot orchestration and error envelopes
├── interactive.py         # Truthful unavailable-provider status after confirmation
├── cli.py                 # Existing read-user-tabs and interactive-browser dispatch
└── schema.py              # Existing envelope shape, unchanged unless tests expose schema drift

tests/browser_fetch_router/
├── test_cdp.py            # New CDP command-client, extraction, screenshot, target validation tests
├── test_cli_contract.py   # CLI JSON/error contract regression tests if dispatch changes
├── test_read_user_tabs.py # Add only if behavior coverage no longer fits focused cdp tests
└── test_round*_*.py       # Existing regression suites must remain green
```

**Structure Decision**: Keep the implementation in the current single-package
layout. Do not add provider modules or adapter code. `cdp.py` owns protocol
transport and protocol-specific exceptions; `read_user_tabs.py` owns approval
checks and user-facing envelopes.

## Complexity Tracking

No constitution violations.

## Post-Design Constitution Check

- **Shared CLI Ownership**: PASS. Design artifacts touch only shared CLI modules
  and tests.
- **Explicit Authorization And Secret Safety**: PASS. CLI contract requires
  approval before text or screenshot capture and forbids credential-bearing
  evidence.
- **Test-First Reliability**: PASS. Tasks must be generated with tests first for
  every user story because the feature spec and constitution require TDD.
- **Portable Installation Contracts**: PASS. Dependency metadata and install
  verification are explicit quickstart requirements.
- **Single Path System Design**: PASS. Research rejects alternate browser
  fallback implementation for this PR and keeps one CDP path.
