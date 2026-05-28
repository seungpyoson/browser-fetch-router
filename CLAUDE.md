# Browser Fetch Router

This repository packages `browser-fetch-router` as a standalone Python CLI and
multi-agent plugin.

## Working Rules
- Keep provider routing, approvals, cache, cost, audit, and lifecycle logic in
  the shared CLI. Agent adapters must stay thin.
- Do not duplicate provider logic inside plugin manifests or skills.
- Verify changes with `python3 -m pytest tests/browser_fetch_router`.
- Verify package installability with `pip install .` and
  `browser-fetch-router --help` from outside the repository.
- Never store API keys in plugin files or skills. Pass credentials only through
  documented environment variables.

## CLI
- Public URL: `browser-fetch-router read-web <url> --json`
- User tab list: `browser-fetch-router read-user-tabs list --json`
- User tab read: `browser-fetch-router read-user-tabs read <url-or-tab-id> --json`
- Interactive task: `browser-fetch-router interactive-browser "<task>" --json`
- Diagnostics: `browser-fetch-router doctor --json`
- Schema: `browser-fetch-router schema --json`


<!-- SPECKIT START -->
For additional context about technologies to be used, project structure,
shell commands, and other important information, read the current plan:
`specs/003-browser-reliability/plan.md`
<!-- SPECKIT END -->
