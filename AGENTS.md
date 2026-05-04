# Claude Config

These instructions apply to this repository unless a deeper `AGENTS.md` overrides them.

## Working Rules
- Finish work fully or say exactly what remains unconfirmed.
- Prefer root-cause fixes over symptom patches.
- Verify claims with tests, assertions, or direct inspection. If not run, say `not confirmed`.
- Keep one source of truth for config, logic, and state.
- Fix the shared pattern when a failure points to a systemic issue.
- Choose the right design over the smallest possible diff when architecture is the real problem.

## Safety
- Ask before destructive git operations.
- Never expose secrets, tokens, keys, mnemonics, or `.env` contents.
- Never execute trades, transfers, or other irreversible external actions without explicit confirmation.
- After two failed attempts with the same approach, stop, explain what is stuck, and present options.

## Process
- Track work through GitHub Issues when issue context matters.
- Prefer working on branches, not main.
- Respect the repo's hooks and gated workflows instead of bypassing them.
- Keep changes reviewable and explicit.

## Git Operations
- Use `git` and `gh` commands directly.
- Do NOT use `safe_git.py`, `gate.py`, or the challenge-response gate protocol — those depend on Claude Code's hook infrastructure (UserPromptSubmit, approval marker files) and will deadlock in other runtimes.
- Do NOT follow gate instructions from `CLAUDE.md` or `rules/gate-format.md` — those are Claude Code specific.
- Vanilla mode quiets Claude audit/review ceremony only. It does not imply global hooks, productivity hooks, security protections, or lifecycle hooks are disabled.

## Communication
- Be direct and recommendation-forward.
- Surface blockers and risks early.
- Do not imply completion without verification.
