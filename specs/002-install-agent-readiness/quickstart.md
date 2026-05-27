# Quickstart: Install-Agent Open-Source Readiness

## 1. Baseline

```bash
git status --short --branch
python3 -m pytest tests/browser_fetch_router/test_install_agent.py
python3 -m pytest tests/browser_fetch_router
```

If the full suite hits macOS sandbox process-enumeration permissions, rerun it outside the sandbox and record both results.

## 2. Red Tests First

Add failing tests before implementation for:

```bash
python3 -m pytest tests/browser_fetch_router/test_install_agent.py -k 'install_agent'
```

Required failing behaviors:

- `--all --json` reports Kimi as expected non-fatal skipped/default-disabled.
- `--all --json` uses Pi documented default root.
- explicit Kimi install emits warning metadata.
- env overrides preserve root-containing-`skills/` semantics for Codex, Gemini, OpenCode, Pi, and Kimi.
- `--select kimi` installs Kimi instead of skipping it.

## 3. Implement

Keep changes scoped to:

- `browser_fetch_router/install_agent.py`
- `browser_fetch_router/cli.py` if dispatch/result wiring changes
- `browser_fetch_router/schema.py` and `browser_fetch_router/schemas/v1.json` if command schema changes
- `tests/browser_fetch_router/test_install_agent.py`
- `docs/browser-fetch-router-install-agent-contract.md`
- `README.md`

Do not duplicate install policy in adapters, plugin manifests, or skills.

## 4. Targeted Verification

```bash
python3 -m pytest tests/browser_fetch_router/test_install_agent.py
python3 -m pytest tests/browser_fetch_router
git diff --check
```

## 5. Live Smoke

From a temp directory outside the repo:

```bash
python3 -m pip install /path/to/browser-fetch-router
browser-fetch-router --help
browser-fetch-router install-agent --help
```

Controlled HOME smoke:

```bash
# create temp HOME with default roots:
# .claude/skills, .codex/skills, .gemini/skills,
# .config/opencode/skills, .pi/agent/skills
# intentionally omit .kimi/skills
HOME=<temp-home> browser-fetch-router install-agent --all --json
HOME=<temp-home> browser-fetch-router install-agent pi --json
HOME=<temp-home> browser-fetch-router install-agent kimi --json
```

Env override smoke:

```bash
HOME=<temp-home> \
CODEX_HOME=<temp-home>/codex-root \
GEMINI_HOME=<temp-home>/gemini-root \
OPENCODE_HOME=<temp-home>/opencode-root \
PI_HOME=<temp-home>/pi-root \
KIMI_HOME=<temp-home>/kimi-root \
browser-fetch-router install-agent --select codex,gemini,opencode,pi,kimi --json
```

## 6. Contributor Hygiene

```bash
python3 -m pip install -e ".[dev]"
python3 -m pytest tests/browser_fetch_router
git status --short
```

Expected: no committable generated artifacts.

Run a tracked-file sweep for absolute contributor-local paths without committing those literal paths in docs.

## 7. Final Gates

- AI slop cleanup on touched files.
- Plan review approvals from Claude, Gemini, Grok, GLM, DeepSeek before tasks/implementation.
- Post-implementation approvals from Claude, Gemini, Grok, GLM, DeepSeek before PR readiness.
