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

## Issue #4 Evidence

Issue #4 originally reported two classes:

1. Contributor-local artifacts from normal test/install flows could become committable because there was no `.gitignore`.
2. `install_agent.py` hardcoded agent skill paths that do not match real agent discovery contracts.

Current `main` partially fixes class 1:

- `.gitignore:1-15` ignores `__pycache__/`, `*.egg-info/`, `dist/`, `build/`, test/lint caches, `.venv/`, and `.venv-*/`.
- `git status --short` after current local flow is empty.
- `git status --short --ignored` shows local generated artifacts are ignored, including `.venv-ci/`, `.pytest_cache/`, `.ruff_cache/`, `browser_fetch_router.egg-info/`, and `__pycache__/`.
- Hardcoded tracked-path sweep: `git grep -nE '/Users/|/home/[A-Za-z0-9_-]+|C:\\Users'` returned no tracked-file matches.

Remaining #4 gap is class 2 plus documentation/verification:

- README only documents package install/test basics; no install-agent discovery contract or safe contributor verification flow.
- Docs contain write-containment contract for `--adapter-path`, but no default skill-discovery contract for supported agents.
- Current install-agent defaults still encode guessed per-agent paths directly in `browser_fetch_router/install_agent.py:59-65`.

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

Live/local layout evidence on this machine:

- Exists: `~/.claude/skills`, `~/.codex/skills`, `~/.gemini/skills`, `~/.config/opencode/skills`, `~/.pi/agent/skills`.
- Missing: `~/.config/pi/skills`, `~/.kimi/skills`.

Live smoke against temp HOME `/private/tmp/bfr-home.XyJ25J`:

- Setup created: `.claude/skills`, `.codex/skills`, `.gemini/skills`, `.config/opencode/skills`, `.pi/agent/skills`.
- Did not create: `.kimi/skills`, `.config/pi/skills`.
- Command: `env -u CODEX_HOME -u GEMINI_HOME -u KIMI_HOME -u OPENCODE_HOME -u PI_HOME HOME=/private/tmp/bfr-home.XyJ25J python3 -m browser_fetch_router install-agent --all --json`
- Exit: 3.
- Result: Claude/Codex/Gemini/OpenCode ok; Kimi failed `agent_adapter_path_unverified` for `.kimi/skills`; Pi failed `agent_adapter_path_unverified` for `.config/pi/skills`.
- Explicit `install-agent pi --json` in same temp HOME failed for `.config/pi/skills` even though `.pi/agent/skills` existed.
- Explicit `install-agent kimi --json` in same temp HOME failed for `.kimi/skills`, with no inheritance warning or opt-in guidance.

## External Discovery Evidence

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

1. Default installation policy is encoded as one list plus one hardcoded path map. It does not distinguish "safe default in --all" from "supported explicit agent".
2. Kimi is treated as a default write target even though Kimi can inherit from Claude/Codex and a new `~/.kimi/skills` directory can change discovery precedence.
3. Pi default points to `~/.config/pi/skills`, which contradicts current Pi docs and local layout.
4. Tests validate current guessed paths by constructing matching env vars/directories; they do not encode vendor discovery contracts.
5. Docs do not publish an install-agent support matrix, default policy, or "use --adapter-path when unverified" rule.

Rejected or partially mitigated:

1. "No `.gitignore`" is stale on current `main`; generated local artifacts are currently ignored.
2. Tracked contributor-local absolute paths were not found.
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
- Change Pi default to `~/.pi/agent/skills/browser-fetch-router/SKILL.md`, preserving `PI_HOME` override semantics if a clear base-directory contract is defined.
- For agents whose default cannot be verified, return actionable `--adapter-path` guidance instead of silently inventing paths.
- Add a docs support matrix for default path, explicit support, `--all` inclusion, env override, source evidence, and known caveats.
- Update schema/help text if `--all` default set differs from full supported list.
- Keep adapter logic thin; shared CLI owns install policy.

## Required Tests / Smoke Candidates

Red tests before implementation:

- `install-agent --all --json` in controlled temp HOME succeeds for default agents and reports Kimi as skipped/not default rather than failed.
- Pi default resolves to `~/.pi/agent/skills/browser-fetch-router/SKILL.md`.
- Explicit `install-agent kimi --json` can write when explicitly requested and includes inheritance warning metadata.
- `--select kimi` remains accepted.
- Schema/docs reflect default-vs-supported distinction.
- Contributor artifact hygiene remains covered by `.gitignore` and hardcoded-path sweep.

Live smoke after implementation:

- Fresh temp HOME with default agent dirs, including `~/.pi/agent/skills`, no `~/.kimi/skills`.
- `pip install .` from outside repo.
- `browser-fetch-router --help` from outside repo.
- `browser-fetch-router install-agent --help`.
- `browser-fetch-router install-agent --all --json` in controlled temp HOME.
- Explicit `browser-fetch-router install-agent pi --json`.
- Explicit `browser-fetch-router install-agent kimi --json`.
- `git status --short` after standard install/test flow.
- Hardcoded-path sweep over tracked files.

## Open Questions For Reviewers

1. Is excluding Kimi from default `--all` the right single-path behavior, or should `--all` include Kimi but mark it skipped with an explicit status?
2. Should `PI_HOME` mean Pi base home (`~/.pi`) or agent config root (`~/.pi/agent`) after changing the default?
3. Should missing default directories for default agents remain hard failures, or should `--all --json` skip unverified agents with per-agent actionable messages?
4. Should docs prefer agent-native paths or shared `.agents/skills` paths where vendors support them?
