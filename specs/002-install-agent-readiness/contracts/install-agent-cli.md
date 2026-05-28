# Contract: `browser-fetch-router install-agent`

## Supported Agents

The CLI supports these explicit agent names:

- `claude`
- `codex`
- `gemini`
- `kimi`
- `opencode`
- `pi`

## Modes

### Explicit Single Agent

```bash
browser-fetch-router install-agent <agent> --json
```

Expected behavior:

- Resolves the agent's documented default root or env override root.
- Writes `skills/browser-fetch-router/SKILL.md` beneath that root when safe.
- Runs post-install verification.
- Returns `status=ok` with artifacts and verification evidence on success.
- For Kimi, includes a warning that writing Kimi's brand root can change Claude/Codex inheritance behavior.

### Explicit Selection

```bash
browser-fetch-router install-agent --select claude,codex --json
```

Expected behavior:

- Installs only requested agents.
- Kimi is installed when explicitly selected, not skipped.
- Invalid names remain usage errors.
- `--adapter-path` remains mutually exclusive with `--select`.

### Default Multi-Install

```bash
browser-fetch-router install-agent --all --json
```

Expected behavior:

- Attempts default write targets only.
- Reports supported explicit-only agents, including Kimi, as `status=skipped` with a default-disabled reason.
- Returns aggregate `status=ok` when all default writes succeed and only expected skips occur.
- Returns aggregate `status=tool_setup_failed` when any default write target fails unexpectedly.
- Includes per-agent actionable guidance for missing/unverified roots.

## Path Policy

- Claude default root: `~/.claude`.
- Codex default root: `${CODEX_HOME:-~/.codex}`.
- Gemini default root: `${GEMINI_HOME:-~/.gemini}`.
- Kimi explicit root: `${KIMI_HOME:-~/.kimi}`; not included as a default write target.
- OpenCode default root: `${OPENCODE_HOME:-~/.config/opencode}`.
- Pi default root: `${PI_HOME:-~/.pi/agent}`.
- Destination path for all roots: `skills/browser-fetch-router/SKILL.md`.

## Result Status Semantics

- `ok`: adapter was written and post-install verification passed.
- `skipped`: no write attempted because the agent is supported but default-disabled or unverified by policy; must include `skip_reason`.
- `tool_setup_failed`: requested/default write failed unexpectedly; must include actionable `error`.

## Warning Semantics

Kimi explicit installs must include warning metadata in JSON. The warning must be visible in both single-agent and `--select kimi` flows and must not be emitted for `--all` default-disabled skip unless the skip reason already covers inheritance.

Single-agent installs expose warnings at top-level `warnings`. Multi-agent installs, including `--select kimi`, expose warnings on the affected `results[]` entry so callers can associate each warning with the agent that produced it.

Warning shape:

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

Default-disabled skip shape:

```json
{
  "status": "skipped",
  "skip_reason": {
    "code": "default_disabled",
    "message": "Kimi is supported only by explicit opt-in because its brand skill root can change Claude/Codex inheritance behavior."
  }
}
```

Per-agent result order is stable: `claude`, `codex`, `gemini`, `kimi`, `opencode`, `pi`.

## Existing Error Compatibility

- Invalid `--adapter-path` stays `tool_setup_failed` with `invalid_adapter_path`.
- Existing `adapter_exists` behavior remains unless `--force` is passed.
- Existing verification subprocess safe-env filtering remains.

## Live Smoke Contract

Final PR evidence must include:

- outside-repo `pip install .`;
- outside-repo `browser-fetch-router --help`;
- `browser-fetch-router install-agent --help`;
- controlled HOME `install-agent --all --json`;
- explicit Pi and Kimi installs;
- env override selection for Codex, Gemini, OpenCode, Pi, and Kimi;
- clean `git status --short` after documented contributor flow;
- tracked-file hardcoded-path sweep.
