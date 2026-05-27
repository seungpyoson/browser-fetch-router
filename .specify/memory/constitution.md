<!--
Sync Impact Report
Version change: template -> 1.0.0
Modified principles:
- PRINCIPLE_1_NAME -> I. Shared CLI Ownership
- PRINCIPLE_2_NAME -> II. Explicit Authorization And Secret Safety
- PRINCIPLE_3_NAME -> III. Test-First Reliability
- PRINCIPLE_4_NAME -> IV. Portable Installation Contracts
- PRINCIPLE_5_NAME -> V. Single Path System Design
Added sections:
- Operational Constraints
- Review And Release Workflow
Removed sections:
- Placeholder section names and examples
Templates requiring updates:
- .specify/templates/plan-template.md: no change needed; Constitution Check section is generic
- .specify/templates/spec-template.md: no change needed; requirements stay user-value focused
- .specify/templates/tasks-template.md: no change needed; user requested TDD so test tasks are required
Follow-up items: none
-->

# Browser Fetch Router Constitution

## Core Principles

### I. Shared CLI Ownership
Provider routing, URL safety, approvals, cache, cost controls, audit logging, and
lifecycle management MUST live in the shared `browser-fetch-router` CLI. Agent
adapters, plugin manifests, and skill files MUST stay thin and MUST NOT duplicate
provider policy or routing logic.

### II. Explicit Authorization And Secret Safety
Authenticated browser state and user tabs MUST require explicit approval scopes
before content or screenshots are exposed. Default-deny hosts and non-HTTP(S)
schemes MUST fail closed. API keys, session cookies, credentials, and private
tokens MUST NOT be stored in plugin files, skill files, specs, plans, tests, or
logs; credentials MAY only flow through documented environment variables.

### III. Test-First Reliability
Every bug fix and feature change MUST start with a failing behavior test through
the public CLI or public Python interface that captures the user-visible
failure. Implementation MAY start only after the failing test is observed.
Refactors MUST preserve passing regression tests, and final verification MUST
include `python3 -m pytest tests/browser_fetch_router` unless a documented
platform permission issue blocks an unrelated test.

### IV. Portable Installation Contracts
Installation behavior MUST be grounded in documented agent discovery contracts,
not guessed local machine paths. Defaults MUST avoid hardcoded contributor
paths, version-sensitive assumptions, and host-specific directories unless they
are explicitly documented and tested. Package installability MUST be verified
with `pip install .` and `browser-fetch-router --help` from outside the
repository before release.

### V. Single Path System Design
The project MUST prefer one systematic implementation path over parallel
fallbacks, hardcoded policy branches, or compatibility shims that preserve
obsolete behavior. When behavior changes, the old path MUST be removed or
converted into the new shared path. Work MUST stay scoped to one issue or
consolidated issue group at a time.

## Operational Constraints

- Public URL reads, user-tab reads, interactive browser tasks, diagnostics, and
  schema output MUST remain exposed through the documented CLI commands in
  `CLAUDE.md`.
- New dependencies MUST be declared in `pyproject.toml` only in the same change
  that imports and uses them.
- Browser automation and CDP extraction MUST preserve approval checks before
  reading DOM text, screenshots, or authenticated page state.
- Generated local artifacts such as virtual environments, bytecode caches,
  pytest caches, and egg-info directories MUST remain ignored.

## Review And Release Workflow

- Root-cause claims for open reliability issues MUST be validated against
  current source and issue evidence before planning.
- Each implementation plan MUST be reviewed by Claude, Gemini, Grok, GLM, and
  DeepSeek, and all five MUST approve before task generation begins.
- Task generation MUST preserve TDD order: failing test, minimal implementation,
  green verification, refactor.
- Completed development MUST receive approving reviews from the same five
  reviewers before a PR is considered ready.
- PRs MUST NOT be merged without explicit user approval.

## Governance

This constitution supersedes conflicting project-local planning conventions.
Amendments require an updated Sync Impact Report, semantic version bump,
template consistency check, and explicit mention in the relevant PR. MAJOR
versions remove or redefine principles; MINOR versions add or materially expand
principles; PATCH versions clarify wording without changing obligations.

**Version**: 1.0.0 | **Ratified**: 2026-05-27 | **Last Amended**: 2026-05-27
