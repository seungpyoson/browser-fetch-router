# Contract: Global Install Verification

## Goal

Prove that the real user-facing `browser-fetch-router` command resolves to the reviewed package state, not a stale local source checkout or old pipx environment.

## Required Checks

- `command -v browser-fetch-router` returns the expected global shim path.
- If the shim is a symlink, the target venv path is reported.
- `browser-fetch-router --help` exposes all expected commands.
- `browser-fetch-router schema --json` contains expected schema version and branch-specific defaults.
- `browser-fetch-router doctor --json` returns `ok` outside sandbox restrictions.
- `browser-fetch-router install-agent --all --json --force` succeeds for default agents and skips explicit-only Kimi by design in a controlled temp HOME or documented global target.
- Installed adapter files contain `browser-fetch-router` and no secrets.
- A public `read-web` smoke runs from outside the repository.

## Stale Install Failure

If schema defaults, command behavior, adapter text, or package location do not match the reviewed branch, verification must fail with a clear stale-install finding and a reinstall instruction.

## Acceptance Commands

```bash
command -v browser-fetch-router
browser-fetch-router --help
browser-fetch-router schema --json
browser-fetch-router doctor --json
browser-fetch-router read-web https://example.com --json --no-cache
```

Installability from the repository remains:

```bash
BFR_TMPDIR="${TMPDIR:-/tmp}"
BFR_INSTALL_VENV="${BFR_TMPDIR%/}/bfr-install-verify"
python3 -m venv "$BFR_INSTALL_VENV"
"$BFR_INSTALL_VENV/bin/pip" install .
cd "$BFR_TMPDIR"
"$BFR_INSTALL_VENV/bin/browser-fetch-router" --help
```
