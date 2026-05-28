# Research: Install-Agent Open-Source Readiness

## Decision: Consolidate issues #4 and #5

**Rationale**: Current state shows #4's original artifact-hygiene symptom is
partially mitigated by `.gitignore`, while the remaining live #4 risk is the
same install-agent path/discovery readiness class described concretely by #5.
A single PR can preserve both acceptance criteria by proving artifact hygiene,
documenting the support matrix, and fixing install-agent defaults.

**Alternatives considered**:

- Keep #4 and #5 separate. Rejected because #4 would still need install-agent
discovery work that duplicates #5.
- Close #4 as stale and handle only #5. Rejected because #4 still requires
package/installability and contributor-hygiene proof in the final PR.

## Decision: Use one table-driven install policy

**Rationale**: A single `AgentInstallContract` table can express supported
agent, default inclusion, env var, default root resolver, warning text, source
evidence, and unverified guidance. This satisfies the single-path constitution
rule while avoiding scattered per-agent branches.

**Alternatives considered**:

- Hardcode conditionals in `destination_for`. Rejected because that repeats the
current maintenance failure.
- Move policy into adapters. Rejected because adapters must stay thin and shared
CLI owns install logic.

## Decision: Kimi is explicit-only by default

**Rationale**: Kimi's brand path is documented, but Kimi also discovers Claude
and Codex brand roots by priority. Creating `~/.kimi/skills` by default changes
that inheritance behavior. Default multi-install should surface Kimi as skipped
or default-disabled, while explicit `install-agent kimi` and `--select kimi`
remain available and warn about inheritance effects.

**Alternatives considered**:

- Keep Kimi in `--all`. Rejected because it can create a higher-priority brand
root without the user explicitly choosing that policy.
- Remove Kimi support entirely. Rejected because #5 requires explicit Kimi to
keep working.

## Decision: Pi default root is `~/.pi/agent`

**Rationale**: Current Pi docs list global skills under `~/.pi/agent/skills/`.
The current code uses `~/.config/pi/skills/`, which fails in local layout and
in the controlled smoke where the documented Pi root exists.

**Alternatives considered**:

- Keep old `~/.config/pi` fallback. Rejected because it preserves a guessed path.
- Try both locations. Rejected because dual defaults violate the single-path
rule and blur which contract is authoritative.

## Decision: Preserve env override semantics

**Rationale**: Current behavior treats `CODEX_HOME`, `GEMINI_HOME`,
`KIMI_HOME`, `OPENCODE_HOME`, and `PI_HOME` as roots containing `skills/`.
Baseline smokes confirm selected installs work when these roots exist. The
feature should preserve that model while changing only Pi's built-in default.

**Alternatives considered**:

- Redefine `PI_HOME` as `~/.pi`. Rejected because it would silently change
override semantics and require migration.
- Add `PI_AGENT_HOME`. Rejected because it introduces a dual path for this PR.

## Decision: `--all --json` expected skips are non-fatal

**Rationale**: The prompt requires `install-agent --all --json` to be reliable
and explain skipped/unverified agents. Expected policy skips, such as Kimi
default-disabled, should appear in results but not force a nonzero status.
True setup failures for default agents should still make the summary nonzero.

**Alternatives considered**:

- Omit Kimi entirely from `--all` output. Rejected because it is less
explainable for users and weakens issue #5 acceptance evidence.
- Return nonzero for every skip. Rejected because that preserves the current
failure mode for known expected skips.

## Decision: Add install-agent contract doc

**Rationale**: Future agent additions need a reviewable place to record source
evidence and caveats. A docs contract keeps the policy visible and testable
without bloating README.

**Alternatives considered**:

- README-only documentation. Rejected because README should stay concise.
- Source comments only. Rejected because users and reviewers need a public
support matrix.
