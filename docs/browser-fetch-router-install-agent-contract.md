# Browser Fetch Router Install-Agent Contract

`browser-fetch-router install-agent` installs thin adapter `SKILL.md` files
that call the shared CLI. Provider routing, approvals, cache, cost controls,
audit, and lifecycle logic stay in `browser-fetch-router`.

## Support Matrix

| Agent | Explicit install | Included in `--all` | Default root | Env override | Source evidence | Caveat |
| --- | --- | --- | --- | --- | --- | --- |
| Claude | Yes | Yes | `~/.claude` | none | Claude Code personal skills use `~/.claude/skills/<name>/SKILL.md`. | Project-local installs use `--adapter-path` when needed. |
| Codex | Yes | Yes | `~/.codex` | `CODEX_HOME` | OpenAI skill installer uses `$CODEX_HOME/skills/<name>` and defaults to `~/.codex/skills`. | Override root must contain `skills/`. |
| Gemini | Yes | Yes | `~/.gemini` | `GEMINI_HOME` | Gemini CLI skills tooling loads skills from `.gemini/skills`. | Override root must contain `skills/`. |
| Kimi | Yes | No | `~/.kimi` | `KIMI_HOME` | Kimi brand skill priority checks `~/.kimi/skills`, then Claude and Codex skill roots. | Explicit installs warn that writing Kimi's brand root can change inheritance. |
| OpenCode | Yes | Yes | `~/.config/opencode` | `OPENCODE_HOME` | OpenCode global skills live under `~/.config/opencode/skills`. | Override root must contain `skills/`. |
| Pi | Yes | Yes | `~/.pi/agent` | `PI_HOME` | Pi global skills live under `~/.pi/agent/skills`. | Pi migration: older guessed `.config/pi` paths are not default targets. |

All roots resolve to:

```text
<root>/skills/browser-fetch-router/SKILL.md
```

## Result Semantics

- `ok`: adapter was written and post-install verification passed.
- `skipped`: no write was attempted because the agent is supported but disabled
  for default multi-install; the entry includes `skip_reason`.
- `tool_setup_failed`: the requested/default write failed; the entry includes an
  actionable `error`, including `--adapter-path` guidance for unverified roots.

Explicit Kimi installs include:

```json
{
  "warnings": [
    {
      "code": "kimi_brand_root_inheritance",
      "message": "Writing Kimi's brand skill root can change Claude/Codex skill inheritance behavior."
    }
  ]
}
```

Single-agent installs expose warnings at top-level `warnings`. Multi-agent
installs, including `--select kimi`, expose warnings on the affected
`results[]` entry so callers can associate each warning with the agent that
produced it.

Default multi-install reports Kimi as:

```json
{
  "agent": "kimi",
  "status": "skipped",
  "skip_reason": {
    "code": "default_disabled",
    "message": "Kimi is supported only by explicit opt-in because its brand skill root can change Claude/Codex inheritance behavior."
  }
}
```

## Adapter Path

`--adapter-path` remains an explicit escape hatch for project-local or custom
agent layouts. The destination basename must be `SKILL.md`; directory paths and
other filenames are rejected before writing. `--adapter-path` cannot be
combined with `--all` or `--select`; custom destinations are only valid for an
explicit single-agent install.

## Contributor Verification

Before marking install-agent changes ready:

```bash
python3 -m pytest tests/browser_fetch_router/test_install_agent.py
python3 -m pytest tests/browser_fetch_router
git diff --check
browser-fetch-router doctor --global-install --json
```

Package installability must be checked from outside the repository with
`pip install .` against the checkout path, followed by
`browser-fetch-router --help`.

The global install verifier reports the resolved shim path, symlink target when
present, schema defaults, and doctor health. If the real global command does
not match the reviewed package contract, it returns `stale_global_install` with
a `pipx reinstall --force .` reinstall instruction before adapter smoke results
are trusted.

Contributor runs should leave generated virtualenvs, caches, bytecode, and
package metadata ignored. A tracked-file path sweep should also report no
machine-local absolute paths.
