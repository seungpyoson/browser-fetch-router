# Data Model: Install-Agent Open-Source Readiness

## AgentInstallContract

Represents one supported agent's install-discovery policy.

Fields:

- `name`: canonical CLI agent name (`claude`, `codex`, `gemini`, `kimi`, `opencode`, `pi`).
- `env_var`: optional environment variable that overrides the agent root.
- `default_root`: documented user-level root used when no env override exists.
- `skill_relative_path`: path below the root, always `skills/browser-fetch-router/SKILL.md`.
- `supported_explicit`: whether `install-agent <agent>` and `--select <agent>` are allowed.
- `included_in_all`: whether default `--all` attempts a write by default.
- `skip_reason`: optional default-skip reason for supported explicit-only agents.
- `warning`: optional warning returned when explicit install can affect discovery semantics.
- `source`: docs/local evidence used to justify the default.

Validation rules:

- `name` must be one of the CLI-supported agent choices.
- `supported_explicit` must be true for every current supported agent.
- `included_in_all` may be false only with a non-empty `skip_reason`.
- Env override roots mean "root containing `skills/`" for every agent that has `env_var`.
- Built-in defaults must come from documented source evidence or return unverified guidance.

## InstallResultEntry

Represents one per-agent JSON result in single or multi-install output.

Fields:

- `agent`: canonical agent name.
- `status`: `ok`, `skipped`, or `tool_setup_failed`.
- `artifacts`: written adapter paths and verification artifacts.
- `warning`: optional explicit-install warning.
- `skip_reason`: optional expected non-fatal skip reason.
- `error`: structured error for actionable failures.
- `evidence`: verification evidence for successful writes.

Validation rules:

- `status=ok` requires at least one adapter artifact and post-install verification evidence.
- `status=skipped` requires `skip_reason` and must not include write artifacts.
- `status=tool_setup_failed` requires `error.code` and actionable message.
- Expected default-disabled skips do not make the aggregate summary fail.
- Default-agent setup failures do make the aggregate summary fail.

## InstallSummary

Aggregate envelope for `install-agent --all` and `--select`.

Fields:

- `status`: aggregate command status.
- `results`: ordered list of `InstallResultEntry`.
- `artifacts`: flattened successful artifacts.
- `evidence`: no aggregate summary evidence; per-agent verification evidence lives
  on each `results[]` entry.

Validation rules:

- `--all` result order should remain stable and documented.
- `--all` must make Kimi's default-disabled skip visible.
- `--select` should include only requested agents; explicit Kimi is not skipped.
- Aggregate status is `ok` when every entry is `ok` or expected `skipped`.
- Aggregate status is `tool_setup_failed` when any default/requested write fails unexpectedly.

## ContributorHygieneEvidence

Verification record proving open-source readiness for contributor workflows.

Fields:

- `commands`: install/test/status commands executed.
- `git_status_short`: expected empty tracked/untracked status after standard flow.
- `ignored_artifacts`: generated ignored artifacts observed during the flow.
- `hardcoded_path_sweep`: tracked-file sweep command and result summary.
- `package_installability`: outside-repo install/help evidence.

Validation rules:

- Generated artifacts must be ignored rather than committed.
- Sweep patterns must not require committing contributor-local absolute paths.
- Package installability evidence must come from outside the repo.
