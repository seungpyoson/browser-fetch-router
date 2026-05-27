# Root Cause Packet: Install-Agent Open-Source Readiness

Issues: #4, #5
Branch: `fix/4-5-install-agent-readiness`
Date: 2026-05-28

## Live State

- `origin/main` synced before investigation: `main...origin/main` was `0 0`.
- Open GitHub issues: #4 and #5 only.
- Open GitHub PRs: none.
- Current branch for artifacts: `fix/4-5-install-agent-readiness`.
- Full baseline outside sandbox: `python3 -m pytest tests/browser_fetch_router` -> 655 passed.
- Sandbox baseline note: same suite hit one macOS sandbox-only `psutil` process enumeration `PermissionError`; rerun outside sandbox passed.

## Constitution Evidence

Relevant constitution constraints from `.specify/memory/constitution.md`:

- Shared CLI owns provider routing, URL safety, approvals, cache, cost controls, audit logging, lifecycle management, and install behavior; agent adapters and plugin manifests stay thin.
- Test-first reliability: every bug fix starts with a failing behavior test through the public CLI or public Python interface; final verification includes `python3 -m pytest tests/browser_fetch_router`.
- Portable installation contracts: installation behavior must be grounded in documented agent discovery contracts, not guessed local paths or host-specific directories.
- Single path system design: prefer one systematic implementation path over parallel fallbacks or compatibility shims; work stays scoped to one issue or consolidated issue group.
- Review/release workflow: root-cause claims for open reliability issues must be validated against current source and issue evidence before planning; implementation plans and completed work need the required external reviews before readiness.

## Issue #4 Evidence

Issue #4 originally reported two classes:

1. Contributor-local artifacts from normal test/install flows could become committable because there was no `.gitignore`.
2. `install_agent.py` hardcoded agent skill paths that do not match real agent discovery contracts.

Live issue #4 expected outcome, paraphrased from `gh issue view 4` on 2026-05-28 KST:

- add tight ignores for standard contributor-local virtualenv, cache, bytecode, and packaging artifacts;
- validate install-agent defaults against real layouts, or fail early with clear `--adapter-path` guidance;
- fresh clone plus standard install/test should leave no committable artifacts and no silent default failures.

Current `main` partially fixes class 1:

- `.gitignore:1-15` ignores `__pycache__/`, `*.egg-info/`, `dist/`, `build/`, test/lint caches, `.venv/`, and `.venv-*/`.
- `git status --short` after current local flow is empty.
- `git status --short --ignored` shows local generated artifacts are ignored, including `.venv-ci/`, `.pytest_cache/`, `.ruff_cache/`, `browser_fetch_router.egg-info/`, and `__pycache__/`.
- Hardcoded tracked-path sweep for absolute macOS, Linux, and Windows user-home paths returned no tracked-file matches before this packet was added.

Remaining #4 gap is class 2 plus documentation/verification:

- README only documents package install/test basics; no install-agent discovery contract or safe contributor verification flow.
- Docs contain write-containment contract for `--adapter-path`, but no default skill-discovery contract for supported agents.
- Current install-agent defaults still encode guessed per-agent paths directly in `browser_fetch_router/install_agent.py:59-65`.

Issue #4 acceptance criteria still need proof in the final PR:

- standard contributor-local artifacts stay ignored after normal install/test flows;
- tracked files contain no contributor-local absolute paths;
- package installability still works outside the repo;
- install-agent defaults are validated against real layouts or return clear `--adapter-path` guidance;
- fresh clone plus standard install/test creates no committable artifacts and no silent-default failures.

## Issue #5 Evidence

Current code:

- `AGENTS = ["claude", "codex", "gemini", "kimi", "opencode", "pi"]` at `browser_fetch_router/install_agent.py:35`.
- `--all` selects that full list in `browser_fetch_router/cli.py:436-437`.
- Kimi default path is `~/.kimi/skills/browser-fetch-router/SKILL.md` at `browser_fetch_router/install_agent.py:63`.
- Pi default path is `~/.config/pi/skills/browser-fetch-router/SKILL.md` at `browser_fetch_router/install_agent.py:65`.
- Default install refuses to proceed unless `dest.parent.parent` already exists at `browser_fetch_router/install_agent.py:157-165`.

Current tests lock the wrong behavior:

- `tests/browser_fetch_router/test_install_agent.py:66-89` expects `install_agents(module.AGENTS)` to write every default destination, including Kimi and Pi.
- `tests/browser_fetch_router/test_install_agent.py:69-76` fabricates `KIMI_HOME` and `PI_HOME` paths matching current code instead of verifying documented discovery.
- No test covers Kimi inheritance behavior.
- No test covers Pi default `~/.pi/agent/skills/`.
- No test covers `install-agent --all --json` skipping or warning for unsupported/unverified defaults.

Live issue #5 expected outcome, paraphrased from `gh issue view 5` on 2026-05-28 KST:

- `install-agent --all` succeeds end-to-end in a fresh environment with Pi and Kimi installed;
- Pi default points at the actual Pi skills directory while `PI_HOME` override continues to work;
- Kimi is excluded from default `--all`; explicit Kimi install still works and warns about Claude/Codex inheritance effects;
- tests and docs capture the per-agent discovery contract so future additions are evidence-grounded.

Live/local layout evidence on this machine:

- Exists: `~/.claude/skills`, `~/.codex/skills`, `~/.gemini/skills`, `~/.config/opencode/skills`, `~/.pi/agent/skills`.
- Missing: `~/.config/pi/skills`, `~/.kimi/skills`.

Live smoke against `<temp-home-a>`:

- Setup created: `.claude/skills`, `.codex/skills`, `.gemini/skills`, `.config/opencode/skills`, `.pi/agent/skills`.
- Did not create: `.kimi/skills`, `.config/pi/skills`.
- Command shape: unset all agent-home overrides, set `HOME=<temp-home-a>`, run `python3 -m browser_fetch_router install-agent --all --json`.
- Exit: 3.
- Result: Claude/Codex/Gemini/OpenCode ok; Kimi failed `agent_adapter_path_unverified` for `.kimi/skills`; Pi failed `agent_adapter_path_unverified` for `.config/pi/skills`.
- Explicit `install-agent pi --json` in same temp HOME failed for `.config/pi/skills` even though `.pi/agent/skills` existed.
- Explicit `install-agent kimi --json` in same temp HOME failed for `.kimi/skills`, with no inheritance warning or opt-in guidance.

Override baseline in `<temp-home-b>`:

- Setup created `pi-base/skills` and `kimi-base/skills`.
- `PI_HOME=<temp-home-b>/pi-base python3 -m browser_fetch_router install-agent pi --json` exited 0 and wrote `pi-base/skills/browser-fetch-router/SKILL.md`.
- `KIMI_HOME=<temp-home-b>/kimi-base python3 -m browser_fetch_router install-agent kimi --json` exited 0 and wrote `kimi-base/skills/browser-fetch-router/SKILL.md`.
- Current env override semantics: each `*_HOME` env var points to the agent root that contains `skills/`; the installer appends `skills/browser-fetch-router/SKILL.md`.

All-override smoke in `<temp-home-c>`:

- Setup created `codex-root/skills`, `gemini-root/skills`, `opencode-root/skills`, `pi-root/skills`, and `kimi-root/skills`.
- Command shape: set `CODEX_HOME`, `GEMINI_HOME`, `OPENCODE_HOME`, `PI_HOME`, and `KIMI_HOME` to those roots, then run `python3 -m browser_fetch_router install-agent --select codex,gemini,opencode,pi,kimi --json`.
- Exit: 0.
- Result: all five selected agents wrote `skills/browser-fetch-router/SKILL.md` under the env-provided root and passed post-install verification.

## External Discovery Evidence

- Accessed 2026-05-28 KST.
- Claude Code docs: personal skills live at `~/.claude/skills/<skill-name>/SKILL.md`; project skills live at `.claude/skills/<skill-name>/SKILL.md`.
  Source: https://code.claude.com/docs/en/skills
- Codex system skill-installer states installs go to `$CODEX_HOME/skills/<skill-name>`, defaulting to `~/.codex/skills`.
  Source: https://github.com/openai/skills/blob/main/skills/.system/skill-installer/SKILL.md
- Gemini CLI docs expose skills management commands and an `activate_skill` tool that loads specialized expertise from `.gemini/skills`.
  Source: https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/cli-reference.md and https://github.com/google-gemini/gemini-cli/blob/main/docs/reference/tools.md
- Kimi CLI docs: user brand group is mutually exclusive in priority order `~/.kimi/skills/`, `~/.claude/skills/`, `~/.codex/skills/`; project brand group is similarly `.kimi/skills/`, `.claude/skills/`, `.codex/skills/`.
  Source: https://moonshotai.github.io/kimi-cli/en/customization/skills.html
- OpenCode docs: global config path is `~/.config/opencode/skills/<name>/SKILL.md`, and it also discovers Claude-compatible and agent-compatible paths.
  Source: https://opencode.ai/docs/skills/
- Pi docs: Pi loads global skills from `~/.pi/agent/skills/` and `~/.agents/skills/`; project skills from `.pi/skills/` and `.agents/skills/`.
  Source: https://pi.dev/docs/latest/skills

## Root-Cause Candidates

Confirmed:

1. Policy gap: default installation policy is encoded as one list plus one hardcoded path map. It does not distinguish "safe default in --all" from "supported explicit agent".
2. Kimi's path is documented, but its default inclusion is a policy/UX defect: Kimi can inherit from Claude/Codex, and creating a new `~/.kimi/skills` directory changes brand-group precedence for users who intentionally rely on inheritance.
3. Pi default points to `~/.config/pi/skills`, which contradicts current Pi docs and local layout.
4. Tests validate current guessed paths by constructing matching env vars/directories; they do not encode vendor discovery contracts.
5. Docs do not publish an install-agent support matrix, default policy, or "use --adapter-path when unverified" rule.

Rejected or partially mitigated:

1. "No `.gitignore`" is stale on current `main`; generated local artifacts are currently ignored.
2. Tracked contributor-local absolute paths were not found. The initial narrow sweep was expanded after review and no current tracked matches remain.
3. `--adapter-path` write containment is covered by existing contract/tests and is not the root cause here.

## Consolidation Assessment

Consolidate #4 and #5.

Reason: #4's unresolved live class is install-agent path/discovery readiness, and #5 is the concrete, verified instance of that class. A single systematic fix can preserve both acceptance criteria without blurring them:

- keep/verify artifact hygiene and tracked-path sweep for #4;
- replace guessed install-agent defaults with documented discovery contracts for #5;
- document support/default policy for all supported agents;
- make live smoke tests prove both issue outcomes.

Non-goal: changing adapter content shape or provider routing.

## Expected Fix Boundary

Implement one install-agent discovery policy:

- Keep explicit support for `claude`, `codex`, `gemini`, `kimi`, `opencode`, `pi`.
- Split supported agents from default `--all` agents.
- Exclude Kimi from default `--all` unless explicit opt-in is used.
- Keep explicit `install-agent kimi` and `--select kimi` working, but surface a warning/result note that writing `~/.kimi/skills` can override Claude/Codex brand-group inheritance.
- Change Pi default root to `~/.pi/agent`, so the destination becomes `~/.pi/agent/skills/browser-fetch-router/SKILL.md`.
- Preserve env override semantics: `PI_HOME` and `KIMI_HOME` point to the agent root containing `skills/`; installer appends `skills/browser-fetch-router/SKILL.md`.
- Preserve existing `CODEX_HOME`, `GEMINI_HOME`, and `OPENCODE_HOME` semantics: each env var points to the agent root containing `skills/`.
- For agents whose default cannot be verified, return actionable `--adapter-path` guidance instead of silently inventing paths.
- Add a docs support matrix for default path, explicit support, `--all` inclusion, env override, source evidence, and known caveats.
- Update schema/help text if `--all` default set differs from full supported list.
- Keep adapter logic thin; shared CLI owns install policy.

## Required Tests / Smoke Candidates

Red tests before implementation:

- `install-agent --all --json` in controlled temp HOME succeeds for default agents and reports Kimi as skipped/not default rather than failed.
- `--all --json` exits 0 when every default install either succeeds or produces an expected non-fatal skipped/default-disabled result; true setup failures for default agents remain nonzero.
- Pi default resolves to `~/.pi/agent/skills/browser-fetch-router/SKILL.md`.
- `PI_HOME=<root-with-skills>` preserves current override semantics.
- Explicit `install-agent kimi --json` can write when explicitly requested and includes inheritance warning metadata.
- `KIMI_HOME=<root-with-skills>` preserves current override semantics.
- `CODEX_HOME`, `GEMINI_HOME`, and `OPENCODE_HOME` preserve current override semantics.
- `--select kimi` remains accepted.
- Schema/docs reflect default-vs-supported distinction.
- Contributor artifact hygiene remains covered by `.gitignore` and a machine-path sweep that checks absolute user paths without embedding contributor-local paths in tracked docs.

Acceptance mapping:

- #4 artifact hygiene: `.gitignore` regression, standard install/test `git status --short`, and tracked-path sweep.
- #4 package installability: outside-repo `pip install .` plus `browser-fetch-router --help`.
- #4 install defaults: docs support matrix plus `--all --json`, Pi, Kimi, and env-override smokes.
- #5 Pi: default path test, explicit Pi CLI smoke, and `PI_HOME` override test.
- #5 Kimi: default-skip test, explicit Kimi warning test, `--select kimi`, and `KIMI_HOME` override test.
- Constitution TDD: each behavior above needs a failing public CLI or public Python test before implementation.

Live smoke after implementation:

- Fresh temp HOME with default agent dirs, including `~/.pi/agent/skills`, no `~/.kimi/skills`.
- `pip install .` from outside repo.
- `browser-fetch-router --help` from outside repo.
- `browser-fetch-router install-agent --help`.
- `browser-fetch-router install-agent --all --json` in controlled temp HOME.
- Explicit `browser-fetch-router install-agent pi --json`.
- Explicit `browser-fetch-router install-agent kimi --json`.
- `git status --short` after standard install/test flow.
- Hardcoded-path sweep over tracked files, using patterns for absolute user/machine paths without leaving those literals in committed docs.

## Decisions For Planning

1. Kimi is supported but not a default write target. `--all --json` must be reliable and must make Kimi's default-skip status visible instead of silently omitting or failing it.
2. `PI_HOME` means Pi agent root containing `skills/`. Default root is `~/.pi/agent`.
3. Default agents with missing verified roots should report actionable per-agent skipped/unverified entries in JSON; this avoids one unsupported local agent failing the whole multi-install flow.
4. Agent-native paths are the default install targets. Shared `.agents/skills` paths remain documentation/context for future work or explicit `--adapter-path`, not default behavior in this issue.
5. Project-local skill discovery is documented as vendor context but not added to `install-agent` default behavior in this issue; project-local install remains possible through explicit `--adapter-path` when it points at `SKILL.md`.
