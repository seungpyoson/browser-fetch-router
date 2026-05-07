# browser-fetch-router CLI write containment contract

This document is the **single source of truth** for "safe agent-channel
filesystem writes" in the `browser_fetch_router` package. The CLI is
the gate every agent (Claude, Codex, Gemini, …) goes through when it
requests a filesystem write — `install-agent --adapter-path` and
`read-user-tabs screenshot --output`. Same-user filesystem permissions
do NOT bound the threat: the agent runs AS the user, so it can already
write anywhere the user can. The CLI's job is to refuse to BE the
writing tool for paths that don't match the operation's intent.

The companion file
`tests/browser_fetch_router/test_round17_replication.py` exercises
each invariant via reproduction tests
(`test_class_d_install_agent_rejects_*`).

## Why this exists

PR #737's round-17 external review surfaced two-of-four reviewer
agreement (DeepSeek P2, Kimi P1) that `install-agent --adapter-path`
and `read-user-tabs screenshot --output` accepted any caller-supplied
path with only `parent.exists()` validation. An agent convinced by a
malicious tab to invoke
`bfr install-agent claude --adapter-path ~/.bashrc --force` overwrote
the legitimate `.bashrc` with the SKILL.md template. Same shape for
`bfr read-user-tabs screenshot --output ~/.ssh/authorized_keys` —
PNG bytes corrupted SSH access (DoS via lockout).

The previous design assumed "the user typed the path themselves." This
is wrong: the AGENT can synthesize the path. The CLI is the gate
through which the agent's action is approved. If the user approves
"install agent" or "screenshot tab," the gate must not permit
overwriting `~/.bashrc` or `~/.ssh/authorized_keys`.

Class D round-17 introduced two validators (`validate_skill_md_dest`,
`validate_image_dest`) that gate by file SHAPE (basename, extension),
not by a system-path denylist. The shape-based approach is robust
against paths the operator might legitimately use (`/tmp/output.png`,
`~/Downloads/today.jpg`) without depending on maintaining a denylist
of every sensitive system file. Symlinks on the path are NOT rejected
— see W4 below for the full rationale.

## Threat model

CLI write containment defends against:

- **Agent-channel arbitrary-file overwrite (DoS).** A malicious tab
  convinces an agent to invoke a CLI flag with a path that overwrites
  a user-trusted file. PNG bytes overwriting `~/.ssh/authorized_keys`
  → SSH lockout. SKILL.md text overwriting `~/.bashrc` → shell
  startup error. The blast radius is DoS via filesystem corruption,
  not RCE (PNG bytes and SKILL.md text aren't shell-executable as
  attacker-chosen code).
- **Path traversal via dot-dot.** `--adapter-path
  ~/legit_dir/../.bashrc` resolves to `~/.bashrc`. The basename of
  the LITERAL string is `.bashrc`, not `SKILL.md`, so the
  basename-check rejects it without depending on path-resolution
  semantics.

Symlinks on the path are NOT a meaningful threat for this validator,
even though an early round-17 implementation tried to reject them.
Two facts together close the symlink threat without any extra check:

  - `os.replace` (used by `atomic_write_bytes`) does NOT follow
    symlinks for the destination. A symlinked SKILL.md target gets
    REPLACED with a regular file containing the new SKILL.md bytes;
    the original symlink-target file is untouched. Verified
    experimentally during the F-17a followup adversarial review.
  - A symlinked parent directory just redirects the write to the
    resolved location, but the basename of the write target is still
    SKILL.md (or `*.png`/`*.jpg`/`*.jpeg`), which is a basename the
    operator could have chosen to write directly anyway.

The original round-17 implementation included a
`_reject_symlink_on_path` walk that broke `/tmp/screenshot.png` on
macOS (where `/tmp` is a system symlink to `/private/tmp`). The
followup dropped the symlink rejection — basename + extension is the
real security boundary.

Out of scope:

- **System-path denylists.** Maintaining a denylist of every
  sensitive system file (`/etc/passwd`, `/etc/shadow`, every dotfile
  format) is brittle. Shape-based validation (basename + extension)
  achieves the same result without the maintenance burden.
- **Cross-user attacks.** Filesystem permissions handle cross-user
  attacks. The CLI containment specifically addresses agent-channel
  attacks where the writer (CLI) and the victim file are the same
  user.
- **Runtime path manipulation by the OS.** W4 rests on POSIX
  `os.replace` semantics (the destination is REPLACED, not followed
  through). On exotic filesystems where `os.replace` behaves
  differently (some FUSE mounts, some network filesystems with
  non-POSIX rename semantics) the W4 guarantee is best-effort. The
  basename check still catches the obvious hostile cases regardless
  of underlying filesystem semantics.

## Invariants

| ID | Invariant | Why | Verified at |
|---|---|---|---|
| **W1** | `validate_skill_md_dest(path)` requires basename to be exactly `SKILL.md` | install-agent only ever writes SKILL.md; rejecting on basename catches `~/.bashrc`, `~/.ssh/authorized_keys`, `/etc/passwd` without consulting any denylist | `paths.validate_skill_md_dest:80-85` |
| **W2** | `validate_image_dest(path)` requires extension in `{.png, .jpg, .jpeg}` (case-insensitive) | screenshot writes PNG bytes; rejecting on extension catches every non-image overwrite target | `paths.validate_image_dest:117-121` |
| **W3** | `validate_image_dest(path)` rejects basenames starting with `.` (dotfiles) | dotfiles are user config by convention (.bashrc, .npmrc, .gitconfig); refusing to overwrite them prevents agent-channel corruption of shell/tool config | `paths.validate_image_dest:113-116` |
| **W4** | Symlinks on the path are NOT rejected — `os.replace` doesn't follow them for the destination, so a symlinked target gets replaced with a regular file and the original symlink-target is untouched. Validation rests on basename + extension only. | F-17a followup — the original symlink-rejection broke `/tmp/screenshot.png` on macOS without any security gain | `paths.validate_skill_md_dest`, `paths.validate_image_dest` (no symlink walk) |
| **W5** | `install_agent.destination_for(adapter_path=…)` routes through `validate_skill_md_dest`; failure surfaces as `tool_setup_failed` envelope, not a silent rewrite | The CLI dispatcher MUST surface the rejection so the operator sees the rejected path | `install_agent.destination_for:43-49` |
| **W6** | `read_user_tabs.screenshot_tab(output=…)` routes through `validate_image_dest` BEFORE any CDP work; failure surfaces as `usage_error` envelope | Path validation precedes side effects so a rejected path doesn't cost a CDP round-trip | `read_user_tabs.screenshot_tab:336-348` |
| **W7** | `paths.UnsafeDestination` is the only exception type these validators raise; both call sites catch it explicitly and convert to envelope responses (no internal_error exit) | Distinct exception type makes the boundary explicit; catch-all `Exception` would silently swallow real bugs | `paths.UnsafeDestination` definition + call-site handlers |

(Note: an earlier W8 about `expanduser` was removed — F-N5 round-17
followup-2 review. `expanduser` is called for ergonomics, but it is not
doing security work in either validator: `Path("~/.bashrc").name` is
already `".bashrc"` without expansion, so the basename check fires
either way. Documenting it as an invariant overclaimed.)

## Validator design — shape-based, not denylist-based

The validators reject by FILE SHAPE (basename, extension) not by
SYSTEM PATH (denylist of `/etc`, `/var`, `/.ssh`, etc.). This is
deliberate:

- A denylist NEVER catches every sensitive file. New shells add new
  config files; new packages add new dotfiles; new system-management
  tools add new directories. The denylist is permanently incomplete.
- A shape-based check answers the question "is this the file the
  operation is supposed to write?" — yes for SKILL.md / *.png in
  ANY location; no for everything else, regardless of where it is.
- Operators can legitimately install adapters or save screenshots to
  any location they want (`/tmp/SKILL.md` for testing,
  `/var/log/screenshots/` for an automated capture pipeline). The
  shape check doesn't break those use cases.
- The denylist approach also creates a maintenance trap: every
  reviewer adds "you missed `/private/etc` on macOS" / "you missed
  `~/.config/claude/settings.json`" / etc. Shape-based validation
  has zero such backlog.

## Maintenance

**Adding a new CLI flag that writes a file**:

1. Identify the operation's invariant: what file does this flag
   ALWAYS write? (a SKILL.md? a PNG? an arbitrary log file?)
2. If the invariant is well-bounded (specific name or extension set),
   add a new `validate_<thing>_dest(path)` helper to `paths.py`
   modeled on `validate_skill_md_dest` / `validate_image_dest`.
   Validate basename / extension only — do NOT walk the path for
   symlinks (`os.replace` doesn't follow symlinks for the
   destination, so they are not a meaningful threat for any
   `atomic_write_bytes` caller).
3. Wire the validator into the CLI handler BEFORE any side effect
   (CDP fetch, network call, subprocess spawn).
4. Convert `UnsafeDestination` to a `tool_setup_failed` or
   `usage_error` envelope at the call site — do NOT let it propagate
   to a CLI internal_error.
5. Add reproduction tests to the round-17 test file (or a new
   round-N file) that exercise: hostile basename, hostile extension
   (if applicable), traversal-via-dot-dot. (Do NOT add a
   symlink-on-path check — see W4 for why it's unnecessary AND
   harmful: the original `_reject_symlink_on_path` walk broke
   `/tmp/screenshot.png` on macOS without any security gain. The
   W4 lock tests in `test_paths.py` pin the symlink-safety property
   against `atomic_write_bytes` directly.)

**Operations that write to OPERATOR-CHOSEN locations** (e.g., a future
`bfr export-cache --output <file>`) need the same shape discipline:
extension/basename check (NOT symlink-on-path rejection — see W4).
Do NOT add a flag that takes an arbitrary path with no validation.

**Removing or relaxing a validator** is a security boundary change —
it MUST go through code review with explicit threat-model
discussion. Update this doc in the same commit.

## What this contract does NOT cover

- **Reads of user-controlled paths** (`browser-fetch-router doctor`
  reads metadata from existing config paths) — read-only operations
  don't have the overwrite blast radius.
- **Subprocess argv injection** — covered by lifecycle contract
  invariant L13 (`subprocess.run` list-based argv only) and L14
  (`_safe_env` filtering for verification subprocesses).
- **Path containment for the persistence layer** (config_dir,
  state_dir, cache_dir) — those locations are PACKAGE-controlled,
  not caller-controlled, and live under the persistence contract.
- **Operating-system-level file permissions** — handled by
  `atomic_write_bytes`'s 0o600 default + `ensure_private_dir`'s
  0o700 parent. The validator's job is to ensure the WRITE TARGET
  is the right file; the persistence contract ensures the WRITTEN
  bytes have safe permissions.
