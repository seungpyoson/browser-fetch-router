"""Persistence subsystem contract suite.

Single source of truth for "safe persistent storage" in the
browser_fetch_router package. Every persistent file the package writes
is registered in `OBJECT_STORES` or `APPEND_LOGS` with a writer/reader
callable. The parametrized tests below run every applicable invariant
against every store.

Adding a new persistent store:

  1. Use the shared helpers (`atomic_write_bytes` + `read_json_dict` for
     object stores; `append_durable_line` for append logs). Do not
     inline `os.open` / `tempfile.mkstemp` in production code — the
     static guard `test_no_adhoc_persistent_writes` fails the build
     if you do.
  2. Register the store in `OBJECT_STORES` or `APPEND_LOGS` below with
     its writer/reader/path callables.
  3. Run this file — the full applicable contract is automatically
     parametrized against your new store.

Adding a new invariant:

  1. Document it in `docs/browser-fetch-router-persistence-contract.md`.
  2. Add a parametrized test here for every applicable shape.
  3. Run the suite — every existing store gains the new check.

Why this exists: PR #737 went through 14+ rounds of review where each
round found one persistence invariant in isolation (atomic write in r9,
wrong-shape resilience in r14, append durability in r15). The
systematic move is to enumerate ALL invariants up front and verify
every store against them in one closing pass. THIS is that closing
pass — the contract suite + the docs file. After this, "new
persistence bug" requires either a missing invariant in the
enumeration (rare) or a non-persistence bug class (different surface,
not caught by this approach anyway).
"""
from __future__ import annotations

import os
import re
import stat
import time
from pathlib import Path

import pytest


# ============================================================
# Store registry — every persistent file the package writes
# ============================================================


def _approvals_writer():
    from browser_fetch_router import approvals

    approvals.add_approval(
        "hostname:contract-test.example.com",
        session_id="contract-test-session",
        persisted=True,
    )


def _approvals_reader():
    from browser_fetch_router import approvals
    from browser_fetch_router.paths import config_dir

    return approvals._load_unlocked(config_dir() / "approvals.json")


def _approvals_path():
    from browser_fetch_router.paths import config_dir

    return config_dir() / "approvals.json"


def _registry_writer():
    from browser_fetch_router import lifecycle

    lifecycle.register_process(
        "contract-test-sid",
        pid=12345,
        create_time=time.time(),
    )


def _registry_reader():
    from browser_fetch_router import lifecycle

    return lifecycle._read_json(lifecycle.session_registry_path("contract-test-sid"))


def _registry_path():
    from browser_fetch_router import lifecycle

    return lifecycle.session_registry_path("contract-test-sid")


def _cache_writer():
    from browser_fetch_router.cache import CacheStore, cache_key
    from browser_fetch_router.paths import cache_dir

    store = CacheStore(cache_dir() / "web")
    store.write(
        cache_key("contract-route", "https://contract-test.example.com/"),
        {"text": "hello", "headers": {}},
        ttl_seconds=60,
    )


def _cache_reader():
    from browser_fetch_router.cache import CacheStore, cache_key
    from browser_fetch_router.paths import cache_dir

    store = CacheStore(cache_dir() / "web")
    return store.read(cache_key("contract-route", "https://contract-test.example.com/"))


def _cache_path():
    from browser_fetch_router.cache import cache_key
    from browser_fetch_router.paths import cache_dir

    key = cache_key("contract-route", "https://contract-test.example.com/")
    return cache_dir() / "web" / key[:2] / f"{key}.json"


def _audit_writer():
    from browser_fetch_router import audit

    audit.append_audit({"event": "contract-test", "input_url_or_task": "https://x.test/"})


def _audit_path():
    from browser_fetch_router.paths import state_dir

    return state_dir() / "audit.jsonl"


def _cost_mirror_writer():
    from browser_fetch_router import cost
    from browser_fetch_router.paths import state_dir

    ledger = cost.CostLedger(
        state_dir() / "cost.db",
        mirror_path=state_dir() / "cost.jsonl",
    )
    ledger.reserve(
        "contract-session",
        "fxtwitter",
        0.001,
        request_cap=1.0,
        session_cap=1.0,
        daily_cap=1.0,
    )


def _cost_mirror_path():
    from browser_fetch_router.paths import state_dir

    return state_dir() / "cost.jsonl"


def _cost_db_writer():
    from browser_fetch_router import cost
    from browser_fetch_router.paths import state_dir

    ledger = cost.CostLedger(state_dir() / "cost.db")
    ledger.reserve(
        "contract-session",
        "fxtwitter",
        0.001,
        request_cap=1.0,
        session_cap=1.0,
        daily_cap=1.0,
    )


def _cost_db_path():
    from browser_fetch_router.paths import state_dir

    return state_dir() / "cost.db"


# Object-store registry. Each entry MUST satisfy invariants A, B, D, E, F.
# Schema versioning (G) is optional; bounded growth (H) is via TTL or
# operator policy.
OBJECT_STORES = [
    {
        "name": "approvals",
        "writer": _approvals_writer,
        "reader": _approvals_reader,
        "path": _approvals_path,
    },
    {
        "name": "session_registry",
        "writer": _registry_writer,
        "reader": _registry_reader,
        "path": _registry_path,
    },
    {
        "name": "cache",
        "writer": _cache_writer,
        "reader": _cache_reader,
        "path": _cache_path,
    },
]

# Append-log registry. Each entry MUST satisfy invariants A, C, D, E.
# Wrong-shape resilience (B) is n/a (line-by-line consumption).
# Bounded growth (H) is operator-managed via external rotation.
APPEND_LOGS = [
    {
        "name": "audit",
        "writer": _audit_writer,
        "path": _audit_path,
    },
    {
        "name": "cost_mirror",
        "writer": _cost_mirror_writer,
        "path": _cost_mirror_path,
    },
]

# SQLite-store registry. SQLite stores have their own read/write
# semantics (sqlite3 connection, not atomic_write_bytes), so they do
# NOT inherit invariants A (atomic write via os.replace), B (wrong-
# shape JSON resilience), C (append-with-fsync), or F (backup-on-
# corruption). They DO share invariants D (parent 0o700) and E
# (file 0o600) — class-H from round-17. Adding a new SQLite store
# means appending here so invariant E + D parametrize it
# automatically.
SQLITE_STORES = [
    {
        "name": "cost_db",
        "writer": _cost_db_writer,
        "path": _cost_db_path,
    },
]


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Point HOME at tmp_path so config_dir / state_dir / cache_dir all
    land inside an isolated subtree. Every persistent-store path the
    package uses derives from `paths.home()` which reads `$HOME`."""
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


# ============================================================
# Invariant A — atomic write (no torn bytes)
# ============================================================
#
# Every object-store writer MUST route through `paths.atomic_write_bytes`
# (which uses tempfile + os.replace) so concurrent readers never observe
# a partially-written file. Behavioral test: patch the helper, exercise
# the writer, assert the helper was called.


WRONG_SHAPE_PAYLOADS = ['"poisoned"', "[]", "42", "null", "true"]


@pytest.mark.parametrize(
    "store", OBJECT_STORES, ids=lambda s: s["name"]
)
def test_invariant_a_atomic_write_routes_through_helper(
    store, isolated_home, monkeypatch
):
    """Invariant A: object stores write only via atomic_write_bytes.

    A direct `path.write_text(...)` would expose torn bytes to a
    concurrent reader between the start and end of the write. The
    helper uses tempfile + os.replace which is observably atomic on
    POSIX.
    """
    from browser_fetch_router import paths

    calls: list[Path] = []
    real_atomic_write = paths.atomic_write_bytes

    def spy(path, data, **kwargs):
        calls.append(Path(path))
        return real_atomic_write(path, data, **kwargs)

    # Patch every module that imports atomic_write_bytes by name. The
    # function is imported at module-load time so each importer holds
    # its own reference — patching the source module isn't enough.
    monkeypatch.setattr("browser_fetch_router.paths.atomic_write_bytes", spy)
    monkeypatch.setattr("browser_fetch_router.approvals.atomic_write_bytes", spy)
    monkeypatch.setattr("browser_fetch_router.lifecycle.atomic_write_bytes", spy)
    monkeypatch.setattr("browser_fetch_router.cache.atomic_write_bytes", spy)

    store["writer"]()

    expected_path = store["path"]()
    assert any(call == expected_path for call in calls), (
        f"writer for {store['name']!r} did not route through "
        f"atomic_write_bytes. Calls observed: {calls}. Expected: "
        f"{expected_path}. Inline path.write_text/write_bytes is a "
        "torn-write hazard — see persistence-contract invariant A."
    )


# ============================================================
# Invariant B — wrong-shape JSON resilience (object stores)
# ============================================================
#
# Readers must absorb valid-JSON-of-wrong-shape (string, list, number,
# bool, null) without crashing. After the writer planted such bytes,
# the reader returns a usable empty/dict-shaped result and the
# corrupt file is renamed aside. Locked in by r14.


@pytest.mark.parametrize("store", OBJECT_STORES, ids=lambda s: s["name"])
@pytest.mark.parametrize("payload", WRONG_SHAPE_PAYLOADS)
def test_invariant_b_wrong_shape_json_resilience(
    store, payload, isolated_home
):
    """Invariant B: planted valid-JSON-wrong-shape doesn't crash readers."""
    path = store["path"]()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload)

    # Must absorb the corruption without raising.
    result = store["reader"]()
    assert isinstance(result, (dict, type(None))), (
        f"{store['name']!r} reader returned {type(result).__name__} "
        f"for wrong-shape input {payload!r}; expected dict or None"
    )


# ============================================================
# Invariant C — append durability (fsync)
# ============================================================
#
# Append logs MUST call os.fsync after every line so a crash between
# write and pdflush doesn't lose forensic records. Closes r15-01.


@pytest.mark.parametrize(
    "store", APPEND_LOGS, ids=lambda s: s["name"]
)
def test_invariant_c_append_durability_calls_fsync(
    store, isolated_home, monkeypatch
):
    """Invariant C: each append-log line is fsynced before close."""
    fsynced_fds: list[int] = []
    real_fsync = os.fsync

    def spy(fd):
        fsynced_fds.append(fd)
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", spy)

    store["writer"]()

    assert fsynced_fds, (
        f"writer for {store['name']!r} did not call os.fsync. Append-log "
        "writes that don't fsync are silently lost on power loss / "
        "kernel panic before pdflush runs (typically up to 30s) — "
        "see persistence-contract invariant C (r15-01)."
    )


# ============================================================
# Invariant E — permission isolation (0o600)
# ============================================================
#
# All persistent files MUST be created with mode 0o600. Approvals can
# carry credentials; audit reveals attack patterns; even cache entries
# can hold sensitive responses. World-readable files leak across
# local-user boundaries.


@pytest.mark.parametrize(
    "store",
    OBJECT_STORES + APPEND_LOGS + SQLITE_STORES,
    ids=lambda s: s["name"],
)
def test_invariant_e_permission_isolation_0o600(store, isolated_home):
    """Invariant E: written files have permission 0o600 (owner-only).

    Parametrized over OBJECT_STORES + APPEND_LOGS + SQLITE_STORES
    (class-H closure: cost.db was previously unparametrized — created
    by `sqlite3.connect()` at the umask default 0o644, exposing cost
    history to other local users on shared workstations).
    """
    store["writer"]()
    path = store["path"]()
    assert path.exists(), f"{store['name']!r} writer didn't create {path}"

    actual_mode = stat.S_IMODE(path.stat().st_mode)
    assert actual_mode == 0o600, (
        f"{store['name']!r} created with mode {oct(actual_mode)}; "
        "expected 0o600. World/group readability leaks credentials, "
        "audit data, and cache responses across local-user boundaries — "
        "see persistence-contract invariant E."
    )


@pytest.mark.parametrize(
    "store",
    OBJECT_STORES + APPEND_LOGS + SQLITE_STORES,
    ids=lambda s: s["name"],
)
def test_invariant_d_parent_directory_is_0o700(store, isolated_home):
    """Invariant D: parent directory is 0o700 (owner-only traversal).

    Class-H closure: cost.db's parent was `mkdir(parents=True)` only,
    landing at the umask default 0o755. World-traversable parent dirs
    let other local users `cd` into the state tree even when files
    are 0o600 — the directory listing leaks file existence and the
    mtime metadata.
    """
    store["writer"]()
    path = store["path"]()
    parent = path.parent
    actual_mode = stat.S_IMODE(parent.stat().st_mode)
    assert actual_mode == 0o700, (
        f"{store['name']!r} parent {parent!s} has mode {oct(actual_mode)}; "
        "expected 0o700. See persistence-contract invariant D."
    )


# ============================================================
# Invariant F — backup-on-corruption (object stores)
# ============================================================
#
# When a corrupt or wrong-shape file is read, the bytes are renamed to
# `<name>.corrupt-<UTC-stamp>` for forensic recovery. Otherwise the
# next writer atomically wipes the bytes and any forensic signal is
# lost. Locked in by r9 + r14.


@pytest.mark.parametrize("store", OBJECT_STORES, ids=lambda s: s["name"])
def test_invariant_f_backup_on_corruption(store, isolated_home):
    """Invariant F: corrupt/wrong-shape file is renamed to .corrupt-*."""
    path = store["path"]()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('"contract-test-poison"')

    store["reader"]()

    siblings = list(path.parent.glob(f"{path.name}.corrupt-*"))
    assert len(siblings) == 1, (
        f"{store['name']!r} reader did not back up the corrupt bytes; "
        f"found {[p.name for p in path.parent.iterdir()]}. The next "
        "writer would atomically wipe the bytes, destroying forensic "
        "evidence — see persistence-contract invariant F."
    )
    assert not path.exists(), (
        f"{store['name']!r} left the original corrupt file in place; "
        "expected it to be renamed aside"
    )


# ============================================================
# Static guard — no ad-hoc persistent writes
# ============================================================


# --- Persistence static-guard patterns -----------------------------------
#
# Hoisted to module level so the round-17 reproduction tests can scan
# synthetic offender files using the same source of truth.

PERSISTENCE_RAW_O_APPEND_RE = re.compile(r"os\.open\([^)]*O_APPEND")
# Class-C round-17: read-side bypass surface. The original regex caught
# `json.loads(path.read_text(...))` only, missing every other read API
# (read_bytes, open(...).read(), Path(...).read_*). All forms feed the
# same json.loads → typed access pattern that the persistence contract
# centralizes via `read_json_dict`.
PERSISTENCE_RAW_LOADS_TYPED_RES = (
    re.compile(r"json\.loads\s*\(\s*[^)]*\.read_text[^)]*\)"),
    re.compile(r"json\.loads\s*\(\s*[^)]*\.read_bytes[^)]*\)"),
    # `open(path, 'rb').read()` and the contextmanager form `with
    # open(...) as f: json.loads(f.read())`. Match the chain
    # `open(...).read()` whether the open is a context-manager
    # binding or a temporary expression.
    re.compile(r"json\.loads\s*\(\s*open\([^)]*\)\.read\("),
)


def find_persistence_offenders(pkg_root: Path, *, helpers_module_name: str = "paths.py") -> list[str]:
    """Scan a package directory for persistence-contract bypasses.

    Module-level so reproduction tests can invoke it on a synthetic
    offender directory. Production guard below calls it on the real
    `browser_fetch_router/` package.
    """
    helpers_module = (pkg_root / helpers_module_name).resolve()
    offenders: list[str] = []
    for py in pkg_root.rglob("*.py"):
        # Helpers themselves are allowed to use the raw primitives.
        if py.resolve() == helpers_module:
            continue
        text = py.read_text(encoding="utf-8")
        rel = py.relative_to(pkg_root.parent) if py.is_relative_to(pkg_root.parent) else py
        for match in PERSISTENCE_RAW_O_APPEND_RE.finditer(text):
            offenders.append(
                f"{rel}:{text[:match.start()].count(chr(10)) + 1} "
                "uses raw os.open(O_APPEND) — must route through "
                "paths.append_durable_line"
            )
        for raw_loads_re in PERSISTENCE_RAW_LOADS_TYPED_RES:
            for match in raw_loads_re.finditer(text):
                tail = text[match.end(): match.end() + 400]
                if (
                    re.search(r"data\s*(\.get\s*\(|\[)", tail)
                    and "isinstance(data, dict)" not in tail
                ):
                    offenders.append(
                        f"{rel}:{text[:match.start()].count(chr(10)) + 1} "
                        "uses json.loads(<read>...) → typed access "
                        "without isinstance gate — must route through "
                        "paths.read_json_dict"
                    )
    return offenders


def test_no_adhoc_persistent_writes():
    """Production code must use the registered persistence helpers.

    Scans `browser_fetch_router/` for raw patterns that bypass the
    contract:

      - `path.write_text(...)` / `path.write_bytes(...)` against a
        path inside config_dir/state_dir/cache_dir
      - `os.open(..., O_APPEND, ...)` outside the `append_durable_line`
        helper
      - `json.loads(<read>...)` followed by typed access outside the
        `read_json_dict` helper

    A new persistent-store writer added without using the helpers
    trips this test instead of silently re-introducing the
    persistence bug class for some future round to find. Uses the
    module-level `find_persistence_offenders` helper so reproduction
    tests share the same source of truth.
    """
    pkg = Path(__file__).resolve().parents[2] / "browser_fetch_router"
    offenders = find_persistence_offenders(pkg)
    assert not offenders, (
        "Ad-hoc persistent writes detected — bypass of the persistence "
        "contract. See docs/browser-fetch-router-persistence-contract.md.\n"
        + "\n".join(offenders)
    )


# ============================================================
# Class-level coverage check
# ============================================================


def test_every_registered_store_has_known_path_inside_data_dirs(isolated_home):
    """Every registered store's path() resolves to a location under
    config_dir/state_dir/cache_dir. Catches accidental registration of
    a store that escapes the data-directory contract.
    """
    from browser_fetch_router.paths import cache_dir, config_dir, state_dir

    valid_roots = [config_dir().resolve(), state_dir().resolve(), cache_dir().resolve()]

    for store in OBJECT_STORES + APPEND_LOGS:
        path = store["path"]().resolve()
        assert any(
            str(path).startswith(str(root)) for root in valid_roots
        ), (
            f"{store['name']!r} path {path} is not inside any of "
            f"{[str(r) for r in valid_roots]}; persistent stores must "
            "live in the package's owned data dirs"
        )
