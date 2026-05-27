# Implementation Plan: Install-Agent Open-Source Readiness

**Branch**: `fix/4-5-install-agent-readiness` | **Date**: 2026-05-28 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `specs/002-install-agent-readiness/spec.md`

## Summary

Fix the remaining install-agent reliability gaps behind GitHub issues #4 and
#5 by replacing guessed install defaults with an explicit agent discovery
policy. The shared CLI will distinguish default multi-install targets from
explicit opt-in agents, correct Pi's default skills root, keep Kimi explicit
with an inheritance warning, preserve existing env override semantics, and add
docs plus live smoke verification for contributor artifact hygiene and package
installability.

## Technical Context

**Language/Version**: Python 3.11+ package (`pyproject.toml` requires `>=3.11`)  
**Primary Dependencies**: Existing runtime deps only (`psutil`, `websockets`); no new dependency planned  
**Storage**: No new persistent storage; installer writes adapter `SKILL.md` files only  
**Testing**: `pytest`; behavior-first tests through public Python functions and CLI dispatch  
**Target Platform**: macOS/Linux primary; Windows-safe path handling where stdlib `Path` supports it  
**Project Type**: Standalone Python CLI/package  
**Performance Goals**: Install-agent commands remain local filesystem/subprocess checks and complete within existing post-install verification timeout  
**Constraints**: Shared CLI owns install policy; adapters stay thin; no secrets in plugin/skill files; no hardcoded contributor-local paths; no parallel fallback install paths  
**Scale/Scope**: One consolidated issue group (#4/#5), six supported agents, package/install docs, targeted install-agent tests plus live smokes

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **Shared CLI Ownership**: PASS. Install policy remains in `browser_fetch_router/install_agent.py` and CLI/schema/docs; adapters remain content-only templates.
- **Explicit Authorization And Secret Safety**: PASS. No credential storage or browser-state access changes; verification subprocess env allowlist remains in scope.
- **Test-First Reliability**: PASS. Implementation tasks must start with failing behavior tests for default-vs-explicit install, Pi path, Kimi warning, env overrides, and contributor hygiene.
- **Portable Installation Contracts**: PASS. Defaults are grounded in vendor docs and recorded source evidence rather than guessed local paths.
- **Single Path System Design**: PASS. The design uses one table-driven install policy with per-agent fields instead of parallel ad hoc branches.

## Project Structure

### Documentation (this feature)

```text
specs/002-install-agent-readiness/
├── root-cause.md
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── install-agent-cli.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
browser_fetch_router/
├── install_agent.py          # install policy, destination resolution, warnings/skips
├── cli.py                    # install-agent dispatch and selected/default behavior
├── schema.py                 # command flag/result schema updates if needed
└── schemas/v1.json           # static schema mirror if current pattern requires update

tests/browser_fetch_router/
├── test_install_agent.py     # behavior-first unit/CLI tests for install policy
└── test_cli_contract.py      # add only if schema/dispatch contract coverage belongs here

docs/
└── browser-fetch-router-install-agent-contract.md

README.md                    # contributor install/test flow and install-agent docs pointer
```

**Structure Decision**: Keep implementation in the existing single-package CLI
layout. Add one install-agent contract doc under `docs/` for the support matrix
and policy. Do not add adapter-specific runtime logic or plugin manifest logic.

## Complexity Tracking

No constitution violations.

## Phase 0 Research Summary

Research decisions are recorded in [research.md](research.md). Key decisions:

- Consolidate #4 and #5 because #4's remaining live gap is install-agent path/discovery readiness and #5 is the verified concrete instance.
- Use a table-driven `AgentInstallContract` policy to keep one implementation path while supporting per-agent documented differences.
- Treat Kimi as supported explicit-only by default; report the skip in `--all --json` instead of failing or silently omitting it.
- Change Pi default root to `~/.pi/agent` and preserve the env override convention that `*_HOME` points at a root containing `skills/`.
- Keep missing verified default roots actionable in JSON; expected skips/default-disabled agents are non-fatal, true default setup failures remain nonzero.

## Phase 1 Design Summary

Design artifacts:

- [data-model.md](data-model.md) defines `AgentInstallContract`, `InstallResultEntry`, `InstallSummary`, and `ContributorHygieneEvidence`.
- [contracts/install-agent-cli.md](contracts/install-agent-cli.md) defines CLI modes, status semantics, warning/skip metadata, and live smoke expectations.
- [quickstart.md](quickstart.md) defines TDD, targeted verification, outside-repo installability, live smoke commands, and final hardcoded-path sweep.

## Post-Design Constitution Check

- **Shared CLI Ownership**: PASS. Data model and contract keep policy in shared CLI; docs only describe behavior.
- **Explicit Authorization And Secret Safety**: PASS. The existing safe env allowlist remains; no secret-bearing files are introduced.
- **Test-First Reliability**: PASS. Quickstart and future tasks require failing tests before implementation and full suite before readiness.
- **Portable Installation Contracts**: PASS. Contract records source evidence and requires docs/source updates together for future agent changes.
- **Single Path System Design**: PASS. Per-agent behavior is data in one contract table; install flow remains one shared path.
