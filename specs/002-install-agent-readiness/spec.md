# Feature Specification: Install-Agent Open-Source Readiness

**Feature Branch**: `fix/4-5-install-agent-readiness`  
**Created**: 2026-05-28  
**Status**: Draft  
**Input**: Root-cause packet for GitHub issues #4 and #5 after Claude adversarial review approval.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Reliable Default Agent Install (Priority: P1)

An open-source contributor runs the default multi-agent install command in a controlled environment and receives a reliable JSON result: verified default agents install, known non-default agents are reported clearly, and no guessed agent path causes the whole command to fail.

**Why this priority**: This is the primary adoption blocker for a new contributor or user trying to set up browser-fetch-router across supported agents.

**Independent Test**: In a fresh temporary home with verified default agent skill roots present and no Kimi-specific skill root, run the default multi-agent install command and verify it completes with successful installs plus explicit non-fatal Kimi skip/default-disabled evidence.

**Acceptance Scenarios**:

1. **Given** a fresh home with verified default agent skill roots, **When** the user runs the default multi-agent install command, **Then** the command reports successful installs for verified default agents and does not fail because Kimi is not a default write target.
2. **Given** a default agent root is missing or unverified, **When** the user runs the default multi-agent install command, **Then** the JSON result names the affected agent and gives actionable `--adapter-path` guidance instead of silently guessing a path.

---

### User Story 2 - Explicit Pi And Kimi Installs (Priority: P1)

A user who explicitly installs Pi or Kimi gets behavior that matches each agent's discovery contract: Pi uses the documented Pi skills location, and Kimi remains available as an explicit opt-in with a warning about inheritance effects.

**Why this priority**: Issues #4 and #5 both identify Pi/Kimi install behavior as the concrete reliability failure.

**Independent Test**: Run explicit Pi and Kimi install commands in controlled homes and verify path, status, warning metadata, and env override behavior.

**Acceptance Scenarios**:

1. **Given** a Pi install layout with `~/.pi/agent/skills`, **When** the user explicitly installs Pi, **Then** the adapter is written under that skills directory.
2. **Given** a user explicitly chooses Kimi, **When** the install succeeds, **Then** the result warns that writing Kimi's brand skill root can change Claude/Codex inheritance behavior.
3. **Given** an agent-home environment override points at a root containing `skills/`, **When** the user installs that agent, **Then** the adapter is written under the override root without changing existing override semantics.

---

### User Story 3 - Contributor Hygiene And Docs (Priority: P2)

An open-source contributor can follow documented install/test flows without creating committable machine-local artifacts, and can inspect a documented support matrix before choosing default or explicit agent installation.

**Why this priority**: This closes the open-source readiness part of issue #4 and prevents future path guesses from being reintroduced.

**Independent Test**: Follow the documented contributor verification flow, then verify clean git status, package installability outside the repo, and no tracked contributor-local absolute paths.

**Acceptance Scenarios**:

1. **Given** a fresh checkout, **When** the contributor follows the documented install/test verification flow, **Then** generated virtualenv, cache, bytecode, and packaging artifacts are ignored and do not appear as committable changes.
2. **Given** a user wants to understand supported agents, **When** they read the docs, **Then** they see each agent's default behavior, explicit support, env override, source evidence, and caveats.

### Edge Cases

- Kimi brand root does not exist but Claude/Codex skill roots do exist.
- Pi documented root exists while the old `.config/pi` root does not.
- An env override points to an existing custom root containing `skills/`.
- A default agent root is missing or unverified during multi-agent install.
- A user passes an explicit `--adapter-path` to a file not named `SKILL.md`.
- Normal test/install flows create ignored artifacts such as virtualenvs, caches, bytecode, or package metadata.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST document a support matrix for Claude, Codex, Gemini, Kimi, OpenCode, and Pi showing default install behavior, explicit support, env override behavior, source evidence, and caveats.
- **FR-002**: The default multi-agent install mode MUST distinguish default write targets from agents that are supported only by explicit opt-in.
- **FR-003**: The default multi-agent install JSON result MUST make skipped/default-disabled agents visible and non-fatal when the skip is expected by policy.
- **FR-004**: The default multi-agent install JSON result MUST make missing or unverified default roots actionable with clear `--adapter-path` guidance.
- **FR-005**: Explicit Pi installation MUST target the documented Pi agent skills root by default.
- **FR-006**: Explicit Kimi installation MUST remain available and MUST warn that writing Kimi's brand skills root can change Claude/Codex inheritance behavior.
- **FR-007**: Existing agent-home environment override semantics MUST be preserved for Codex, Gemini, Kimi, OpenCode, and Pi.
- **FR-008**: Contributor install/test documentation MUST guide users toward commands that do not leave committable machine-local artifacts.
- **FR-009**: Verification MUST prove tracked files contain no contributor-local absolute paths after the feature artifacts are added.
- **FR-010**: Agent adapters MUST remain thin; install policy belongs to the shared CLI and must not store secrets or credentials.

### Key Entities

- **Agent Discovery Contract**: A supported agent's documented skill roots, default-install eligibility, explicit-install eligibility, env override root, and caveats.
- **Install Result Entry**: Per-agent result containing agent name, status, artifacts, warnings or skip reason, errors, and verification evidence.
- **Support Matrix**: User-facing docs table that records the discovery contract for every supported agent.
- **Contributor Hygiene Check**: Verification evidence that normal contributor flows leave only ignored generated artifacts and no tracked machine-local paths.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In a controlled fresh home, default multi-agent install completes without fatal failure from Kimi being non-default and reports every installed or skipped agent explicitly.
- **SC-002**: Explicit Pi install writes to the documented Pi skills directory in a controlled home.
- **SC-003**: Explicit Kimi install succeeds when requested and includes a warning about inheritance effects.
- **SC-004**: Env override tests for Codex, Gemini, Kimi, OpenCode, and Pi all write under the override root containing `skills/`.
- **SC-005**: Following the documented contributor verification flow leaves `git status --short` empty.
- **SC-006**: Package installability is proven from outside the repository before the PR is considered ready.
- **SC-007**: The full browser-fetch-router test suite passes before the PR is considered ready.

## Assumptions

- Kimi remains a supported explicit target but is not a default write target because Kimi can inherit Claude/Codex skills.
- Agent-home environment variables point to a root that contains a `skills/` child directory.
- Project-local skill roots are vendor context only for this issue; users can still target them explicitly with `--adapter-path`.
- Existing `--adapter-path` write containment is already covered by the CLI write-containment contract and remains unchanged except where docs mention explicit paths.
