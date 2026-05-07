# browser-fetch-router persistence contract

This document is the **single source of truth** for what "safe persistence"
means in the `browser_fetch_router` package. Every persistent file the
package writes — credentials, audit logs, session registries, cache
entries, cost ledgers — is one of three shapes. Each shape has a fixed set
of invariants that MUST hold regardless of who wrote the writer.

The companion file `tests/browser_fetch_router/test_persistence_contract.py`
implements every invariant as a parametrized test. Adding a new persistent
store to the package means **registering it in that test's
`PERSISTENT_STORES` table** — the suite then runs the full applicable
contract against it. A new store that fails any invariant fails the build.

This is the systematic alternative to whack-a-mole reviews: rather than
catching one persistence bug per round, the contract enumerates ALL
invariants up front and verifies every store against them.

## Why this exists

PR #737 has gone through 14 rounds of review. Three of the most recent
rounds — r9 (atomic write), r14 (wrong-shape JSON resilience), r15
(append durability via fsync) — addressed three different invariants of
the **same subsystem**: persistent files. Each round found one invariant
in isolation, fixed it across all known sites, and shipped. The rounds
should have been one upfront enumeration of "what does safe persistent
storage in this package require?" — followed by a single closing pass
verifying every store satisfies every applicable invariant.

This doc + the contract test ARE that one closing pass.

## Store shapes

| Shape | Description | Examples | Implementing helper |
|---|---|---|---|
| **Object store** | Single JSON object, full-file replace on every write. Read-modify-write cycles are the expected mutation pattern. | `approvals.json`, `sessions/<sid>.json`, `cache/<key>.json` | `atomic_write_bytes` + `read_json_dict` + `SentinelLock` |
| **Append log** | JSONL file with append-only semantics. Forensic record; readers tail; never replaced. | `audit.jsonl`, `cost.jsonl` | `append_durable_line` |
| **SQLite** | Multi-table relational store with transactional invariants. | `cost.db` | Custom `_connect` contextmanager + WAL + `synchronous=NORMAL` |

Lock-sentinel files (`.approvals.lock`, `sessions/.<sid>.lock`, etc.) are
NOT a store shape — they hold no data. They only need flock semantics +
0o600 mode, which `SentinelLock` provides.

## Invariants

Each invariant has a single-letter ID for cross-referencing. The applicable
column shows which shapes the invariant applies to.

| ID | Invariant | Why it matters | Object | Append | SQLite |
|---|---|---|:-:|:-:|:-:|
| **A** | Atomic write — no torn bytes visible to a concurrent reader, ever | partial writes corrupt downstream consumers | ✅ | ✅ (line atomic) | ✅ (txn) |
| **B** | Wrong-shape JSON resilience — `json.loads("hacked")` returns a string; readers must absorb non-dict shapes without crashing | r14-01: silent dead approval class | ✅ | n/a (line-by-line) | ✅ (typed schema) |
| **C** | Append durability — `fsync` after every line so a crash between write and pdflush doesn't lose forensic records | r15-01: suppressed audit/SSRF events | n/a | ✅ | ✅ (`synchronous=NORMAL`) |
| **D** | Concurrency safety — multiple writers serialize without losing entries; locks survive atomic replace via sibling-file-lock pattern | round-3 stale-inode race | ✅ (`SentinelLock`) | ✅ (`flock`) | ✅ (`BEGIN IMMEDIATE`) |
| **E** | Permission isolation — file mode `0o600`, parent dir `0o700` so other local users cannot read approvals/audit | secrets in approvals; audit reveals attack patterns | ✅ | ✅ | ✅ |
| **F** | Backup-on-corruption — corrupt or wrong-shape data (top-level OR nested-record-shape) is renamed to `<name>.corrupt-<UTC-stamp>` for forensic recovery via the `paths.backup_corrupt_file` SSOT | r9 + r14 (top-level); round-17 Class G (nested cache record) | ✅ | n/a | n/a |
| **G** | Schema versioning (optional) — readers reject obsolete schemas cleanly | round-3 cache schema bump | optional | optional | ✅ |
| **H** | Bounded growth (operator concern) — files do not grow without bound; rotation is operator-managed unless a TTL applies | exhausted disks lock the user out | TTL (cache); known unbounded (approvals, registry) | known unbounded; rotate externally | DELETE policy |

## Store inventory

Every persistent file the package writes is in this table. New persistent
stores MUST be added here AND to `PERSISTENT_STORES` in the contract test.

| Store | Path | Shape | Writer | Reader | Invariants required | Status |
|---|---|---|---|---|---|---|
| approvals | `~/.config/browser-fetch-router/approvals.json` | Object | `approvals.add_approval`, `approvals.revoke_scope` | `approvals._load_unlocked` | A,B,D,E,F | ✅ |
| session registry | `~/.local/state/browser-fetch-router/sessions/<sid>.json` | Object | `lifecycle.register_process` | `lifecycle._read_json` | A,B,D,E,F | ✅ |
| cache entry | `~/.cache/browser-fetch-router/web/<hash[:2]>/<hash>.json` | Object | `cache.CacheStore.write` | `cache.CacheStore.read` | A,B,E,F,G,H(TTL) | ✅ |
| audit log | `~/.local/state/browser-fetch-router/audit.jsonl` | Append | `audit.append_audit` | external tail | A,C,D,E | ✅ |
| cost mirror | `~/.local/state/browser-fetch-router/cost.jsonl` | Append | `cost.CostLedger._mirror` | external tail | A,C,D,E | ✅ |
| cost ledger | `~/.local/state/browser-fetch-router/cost.db` | SQLite | `cost.CostLedger.{reserve,release}` | `cost.CostLedger.{session_total,daily_total}` | A,C,D,E,G | ✅ (registered round-17 — was previously unparametrized) |

All stores satisfy all applicable invariants — every cell is ✅.

The contract test partitions stores into three Python-level buckets so
parametrization stays clean — `OBJECT_STORES`, `APPEND_LOGS`, and
`SQLITE_STORES`. Invariants D (parent 0o700) and E (file 0o600)
parametrize over the union of all three. SQLite stores intentionally do
NOT inherit invariants A (atomic-write via os.replace), B (wrong-shape
JSON), C (line-by-line fsync), or F (corruption-rename) — the SQLite
read/write semantics are different and the equivalent guarantees come
from the `_connect` contextmanager + WAL + `synchronous=NORMAL`. Adding
a new SQLite store means appending to `SQLITE_STORES` and inheriting
D + E for free; if the new store needs A/B/C/F equivalents, they belong
in the SQLite shape's own contract bullet.

## Maintenance

**Adding a new persistent store** (procedure):

1. Choose a shape: object, append, or sqlite. Most likely object or append.
2. Use the existing helpers (`atomic_write_bytes` + `read_json_dict` for
   object; `append_durable_line` for append; `paths.backup_corrupt_file`
   for nested-shape corruption — the SSOT extracted in round-17 Class G
   so any reader that detects nested-record corruption hands off to the
   same forensics-friendly rename pattern). Do **not** inline `os.open`
   / `tempfile.mkstemp` / `json.dumps(...).encode()` /
   `json.loads(path.read_bytes())` in production code — the static-guard
   test (`test_persistence_contract::test_no_adhoc_persistent_writes`)
   fails the build if you do. The read-side regex tuple covers
   `read_text`, `read_bytes`, and `open(...).read()` — round-17 Class C
   closure of the original `read_text`-only regex.
3. Register the store in the matching bucket (`OBJECT_STORES`,
   `APPEND_LOGS`, `SQLITE_STORES`) in
   `tests/browser_fetch_router/test_persistence_contract.py` with its
   writer/reader/path callables.
4. Run the contract suite — it parametrizes all applicable invariants
   against your new store automatically.
5. Add an entry to the **Store inventory** table above.

**Adding a new invariant**:

1. Document it here with an ID + applicability table.
2. Add a parametrized test in the contract suite for every shape it
   applies to.
3. Run the suite — every existing store gains the new check
   automatically. Fix or document any failures before merge.

**Removing or renaming an existing store** is a schema break — it MUST
go through a deprecation cycle, never a silent removal. Update this doc
in the same commit as the code change.

## What this contract does NOT cover

- **In-memory state** (request rate-limit caches, session memo dicts) —
  not persisted, not in scope.
- **Subprocess argv / env** — covered by `_safe_env` filtering (see
  `install_agent.py`), not by persistence.
- **HTTP response bodies** — those land in cache entries (covered above)
  but the response-decoder pipeline itself is `http_client.py`'s scope.
- **External rotation** — operators run `logrotate` / `launchd` /
  `systemd-timer` against `audit.jsonl` and `cost.jsonl` to enforce
  invariant H. The package does NOT rotate in-process. `doctor.py`
  emits a warning when these files exceed 100 MB so operators have
  observability.
