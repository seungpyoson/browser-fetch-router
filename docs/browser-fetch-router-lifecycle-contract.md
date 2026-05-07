# browser-fetch-router lifecycle contract

This document is the **single source of truth** for "safe process
lifecycle" in the `browser_fetch_router` package. All process
registration, cleanup, signal escalation, and registry handling go
through `lifecycle.py`. This file enumerates the 15 invariants that
subsystem MUST satisfy.

The companion file
`tests/browser_fetch_router/test_lifecycle_contract.py` implements
every invariant as a parametrized test (39 test cases). Adding a new
lifecycle entry point means **routing through `lifecycle.py`** — the
static guard `test_no_adhoc_signaling_in_production_code` fails the
build for any production `os.kill`, `os.killpg`, or
`signal.{SIGTERM,SIGKILL,SIGINT,SIGQUIT}` use outside `lifecycle.py`.

## Why this exists

PR #737 went through 15+ rounds of review. The persistence subsystem
closed via a similar contract; HTTP transport closed via another.
This is the lifecycle closing pass. Rounds 3-12 each found one
invariant of the same subsystem (PID reuse defense, SIGTERM-children-
first, SIGKILL escalation, dry-run side-effect-freeness, registry
unlink gate, path traversal via session_id). The systematic
alternative: enumerate ALL invariants up front and verify every
code path satisfies all of them in one closing pass.

## Threat model

The lifecycle module defends against:

- **PID reuse**: the OS may reuse a freed PID for an unrelated user
  process. Without verification, a stale registry record could
  signal an unrelated process. Defense: L2 — `should_kill_process(...)`
  compares both `pid` AND `create_time` (millisecond precision)
  before any signal.
- **Orphan descendants**: killing the leader first detaches
  descendants — they reparent to PID 1 and `proc.children(...)`
  returns empty mid-walk. Defense: L3 — SIGTERM descendants BEFORE
  the leader.
- **SIGTERM-ignoring processes**: a process that traps SIGTERM (or
  is in D-state) survives the grace period. Defense: L4 — escalate
  to SIGKILL, then if STILL alive, surface as `failed` so registry
  preserves the record for retry.
- **Path traversal via session_id**: a session ID like `../audit`
  composes into `sessions/../audit.json` — `path.unlink()` would
  delete the unrelated audit registry. Defense: L1 — grammar
  `^[A-Za-z0-9_-]{1,64}$` + path containment check.
- **Dry-run side effects**: an operator running `cleanup --dry-run`
  to preview must NOT trigger any signal or unlink. Defense: L5
  + L6.
- **Registry data loss**: a SIGKILL-ignoring survivor whose registry
  is unlinked becomes an invisible orphan — no future `cleanup
  --global-orphan-reap` knows it exists. Defense: L6 — unlink ONLY
  when `not dry_run AND not per_session["failed"]`.
- **Malformed registry entries** (closing-pass): a single bad
  record (wrong-type pid, non-dict entry) crashed the entire
  cleanup CLI with `ValueError` / `AttributeError`. Defense: L15
  — bucket malformed entries under `malformed`, continue with
  valid siblings.
- **Stale-inode race**: locking the data file then `os.replace`-ing
  it leaves the lock on the orphan inode — concurrent writers race
  on the new inode. Defense: L7 — lock the SIBLING `_registry_lock_path`
  file.
- **Subprocess credential leak**: `subprocess.run(env=os.environ)` in
  install verification would inherit `ANTHROPIC_API_KEY` etc.
  Defense: L14 — `_safe_env()` filters to a curated whitelist.
- **Subprocess argv injection**: `shell=True` with caller-influenced
  argv is RCE-equivalent. Defense: L13 — list-based argv only,
  AST-enforced via static guard.

Out of scope:

- **Signal handling for the parent CLI** (Ctrl+C / SIGINT) — the CLI
  dispatcher catches `KeyboardInterrupt` separately; lifecycle does
  not install signal handlers.
- **Cross-machine cleanup** — global_orphan_reap is local to the
  registry directory only.

## The 15 invariants

| ID | Invariant | Why | Verified at |
|---|---|---|---|
| **L1** | Session IDs match `^[A-Za-z0-9_-]{1,64}$` AND the resolved path lives inside `session_registry_dir()`. Both checks at the same boundary so route-arounds can't bypass. | round-6 r6-05 path-traversal class. | `lifecycle.py:42`, `:73-79` |
| **L2** | `should_kill_process(pid, create_time)` called BEFORE any `os.kill` / `terminate` / `kill`. | PID reuse defense. | `:234-240` |
| **L3** | SIGTERM descendants BEFORE the leader (orphan defense). | round-6 r6-03 orphan class. | `:252-268` |
| **L4** | SIGTERM → wait_procs(1.0) → SIGKILL survivors → wait_procs(0.5) → `failed` if alive. | round-12 i06 'lie-about-cleanup' class. | `:271-292` |
| **L5** | `dry_run=True` returns `cleaned` WITHOUT signaling and never unlinks. | Operator must trust preview. | `:240-241` |
| **L6** | Registry unlink only when `not dry_run AND not per_session["failed"]`. | Failed survivor must stay tracked for retry. | `:387-389` |
| **L7** | `register_process` reads/modifies/writes under `SentinelLock` on the SIBLING `_registry_lock_path` file (not the data file). | round-3 stale-inode race class. | `:139-171` |
| **L8** | `_atomic_write_json` routes through `paths.atomic_write_bytes`. | Cross-subsystem invariant; persistence contract handles full coverage. | `:82-83` |
| **L9** | `_read_json` routes through `paths.read_json_dict` (handles missing / parse-error / wrong-shape JSON). | Cross-subsystem invariant. | `:86-105` |
| **L10** | `all_sessions=True` is a no-op without `session_id` — does NOT default to globbing the entire registry. | Use `global_orphan_reap=True` for cross-session reaping. | `:337-340` |
| **L11** | `global_orphan_reap` glob is `registry_dir.glob("*.json")` — non-recursive, no subdirectory recursion. | Bounded blast radius. | `:347` |
| **L12** | `per_session[outcome].append(...)` — direct dict subscript. No `.get(outcome, ...)` fallback. | Fail-loud > defensive-fallback for schema invariants (Gemini medium on 9a26fb2). | `:369-374` |
| **L13** | All `subprocess.run` / `Popen` calls use list-based argv. No `shell=True`. AST-enforced. | RCE class. | `acceptance.py:22-30`, `install_agent.py:155-170` |
| **L14** | `install_agent`'s verification subprocess uses `_safe_env()`, not `os.environ`. | Credential leak between agent contexts. | `install_agent.py:155` |
| **L15** | Malformed registry entries (wrong-type pid/create_time, non-dict entries) bucket under `malformed` and don't crash; valid siblings still cleaned. | CLI-crash robustness. | `lifecycle.py:run_cleanup` (closing-pass fix) |

## Per-process outcome contract

`_kill_pid_safely` returns one of three strings, exposed as
`REGISTRY_OUTCOME_KEYS`:

| Outcome | Meaning |
|---|---|
| `cleaned` | Process tree verifiably gone after the kill sequence (or `dry_run=True`, or psutil missing, or PID already gone). |
| `skipped` | PID/create_time mismatch (record is stale; not ours). |
| `failed` | Survivors exist post-SIGKILL (D-state, permission, kernel issue). Registry preserved for retry. |

Plus the closing-pass `malformed` bucket (added at the
`run_cleanup` level, not `_kill_pid_safely`) for entries that
can't be parsed into pid + create_time.

A new outcome category MUST be added to `REGISTRY_OUTCOME_KEYS` AND
this table — the L12 fail-loud subscript pattern means any
return-value drift in `_kill_pid_safely` raises `KeyError` instead
of being silently miscategorized.

## Static guard

`test_no_adhoc_signaling_in_production_code` walks the AST of every
`.py` file in `browser_fetch_router/` (excluding `lifecycle.py`)
and rejects:

- `os.kill(...)`, `os.killpg(...)`
- `signal.SIGTERM`, `signal.SIGKILL`, `signal.SIGINT`, `signal.SIGQUIT`

AST walk (not regex) so docstring text mentioning these names
doesn't false-positive. Centralizing signaling enforces L2 + L3 +
L4 on every signaling code path.

## Maintenance

**Adding a new lifecycle entry** (registering a new long-lived
process):

1. Use `lifecycle.register_process(...)` — never write directly to
   the registry file.
2. The static guard prevents bypass via raw `os.kill` etc.
3. Run the contract suite — every applicable invariant runs
   automatically.

**Adding a new outcome category**:

1. Add to `REGISTRY_OUTCOME_KEYS` AND document in this file.
2. Update the L12 pre-init loop in `run_cleanup` to allocate the
   bucket.
3. Run the suite — L12 will fail-loud if `_kill_pid_safely` returns
   anything outside the canonical set.

**Adding a new subprocess spawn**:

1. List-based argv only — no `shell=True` (L13).
2. Use `_safe_env()` if the spawn is verification / agent-context
   adjacent (L14).
3. Document the spawn in `acceptance.py` or `install_agent.py`
   surface table.

**Removing or relaxing an invariant** is a security boundary change
— it MUST go through code review with explicit threat-model
discussion.
