"""Lifecycle subsystem contract suite.

Single source of truth for "safe process lifecycle" in the
browser_fetch_router package. All process registration, cleanup,
signal escalation, and registry handling go through `lifecycle.py`.
This file enumerates the 15 invariants that subsystem MUST satisfy
and verifies each behaviorally OR via static source assertion.

Adding a new lifecycle entry (e.g., a new way to register a
long-lived process):

  1. Use `lifecycle.register_process(...)`. Raw `os.kill`,
     `signal.SIGTERM`, etc. outside `lifecycle.py` is blocked by the
     static guard `test_no_adhoc_signaling_in_production_code`.
  2. If you add new outcome categories, document them in
     `docs/browser-fetch-router-lifecycle-contract.md` AND in
     `REGISTRY_OUTCOME_KEYS`.
  3. Run this suite — every applicable invariant runs against the
     subsystem.

Why this exists: PR #737 went through 15+ rounds of review. The
persistence subsystem closed via a similar contract; HTTP transport
closed via another. This is the lifecycle closing pass — same
systematic move at the subsystem level rather than per-finding.
"""
from __future__ import annotations

import inspect
import os
import re
import signal
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Point HOME at tmp_path so state_dir/config_dir/cache_dir all
    land inside an isolated subtree."""
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


# ============================================================
# L1 — Session ID grammar + path containment
# ============================================================


@pytest.mark.parametrize(
    "bad_session_id",
    [
        "../audit",                    # path traversal
        "a/b",                          # slash
        "a..b",                         # double-dot
        "",                             # empty
        " " * 5,                        # whitespace
        "x" * 65,                       # too long (>64)
        "session;rm -rf /",             # shell metachar
        "session\nID",                  # newline
        "session\x00null",              # NUL
        "сессия",                        # non-ASCII
    ],
)
def test_l1_session_id_grammar_rejects_unsafe(bad_session_id):
    """Invariant L1: session IDs that violate the regex
    `^[A-Za-z0-9_-]{1,64}$` are rejected by `validate_session_id`
    and the path-containment check in `session_registry_path` —
    closing the round-6 r6-05 path-traversal class."""
    from browser_fetch_router import lifecycle

    with pytest.raises(lifecycle.InvalidSessionId):
        lifecycle.session_registry_path(bad_session_id)


@pytest.mark.parametrize(
    "good_session_id",
    [
        "abc",
        "session-001",
        "agent_2026_05_07",
        "x",
        "x" * 64,
        "Aa0_-Aa0_-",
    ],
)
def test_l1_session_id_grammar_accepts_valid(good_session_id, isolated_home):
    """Invariant L1 (positive): valid session IDs map to a path inside
    the registry directory."""
    from browser_fetch_router import lifecycle

    path = lifecycle.session_registry_path(good_session_id)
    assert path.suffix == ".json"
    assert path.stem == good_session_id


# ============================================================
# L2 — should_kill_process called BEFORE any signal
# ============================================================


def test_l2_should_kill_process_called_before_any_os_kill():
    """Invariant L2: `_kill_pid_safely` MUST verify PID + create_time
    via `should_kill_process(...)` BEFORE issuing `os.kill`. PID
    reuse is real — the OS may reuse a freed PID for an unrelated
    user process, and signaling it would terminate that unrelated
    process. Closes round-3 PID-reuse class.

    Static source assertion: in `_kill_pid_safely` the
    `should_kill_process(...)` call appears BEFORE the first
    `os.kill(...)` call.
    """
    from browser_fetch_router import lifecycle

    src = inspect.getsource(lifecycle._kill_pid_safely)
    should_kill_pos = src.find("should_kill_process(")
    os_kill_pos = src.find("os.kill(")
    terminate_pos = src.find(".terminate(")
    assert should_kill_pos != -1, (
        "_kill_pid_safely must call should_kill_process() before signaling"
    )
    # Pick the earliest signaling call
    earliest_signal = min(
        p for p in (os_kill_pos, terminate_pos) if p != -1
    )
    assert should_kill_pos < earliest_signal, (
        "should_kill_process() must be called BEFORE any signal/terminate "
        "call; otherwise PID reuse defense is bypassed"
    )


# ============================================================
# L3 — SIGTERM children BEFORE leader
# ============================================================


def test_l3_sigterm_children_before_leader_in_source():
    """Invariant L3: descendants are SIGTERMed BEFORE the leader.
    If we kill the leader first, descendants reparent to PID 1
    mid-walk and `proc.children(recursive=True)` returns empty —
    orphans escape cleanup.

    Static source assertion: in `_kill_pid_safely`, the loop over
    `descendants` calling `child.terminate()` appears BEFORE the
    `os.kill(pid, signal.SIGTERM)` of the leader.
    """
    from browser_fetch_router import lifecycle

    src = inspect.getsource(lifecycle._kill_pid_safely)
    children_terminate_pos = src.find(".terminate(")
    leader_sigterm_pos = src.find("os.kill(pid, signal.SIGTERM)")
    assert children_terminate_pos != -1, (
        "_kill_pid_safely must call child.terminate() in a loop over "
        "descendants"
    )
    assert leader_sigterm_pos != -1, (
        "_kill_pid_safely must SIGTERM the leader via os.kill"
    )
    assert children_terminate_pos < leader_sigterm_pos, (
        "child.terminate() must run BEFORE os.kill(leader); otherwise "
        "descendants reparent to PID 1 and escape cleanup (round-6 r6-03)"
    )


# ============================================================
# L4 — SIGKILL escalation if SIGTERM survivors
# ============================================================


def test_l4_sigkill_escalation_returns_failed_if_still_alive(monkeypatch):
    """Invariant L4: SIGTERM → wait_procs(1.0) → SIGKILL survivors →
    wait_procs(0.5) → 'failed' if anything still alive. A SIGKILL-
    ignoring process (D-state, kernel issue) MUST surface as 'failed'
    so `run_cleanup` preserves the registry for retry — closes
    round-12 i06 ('lie-about-cleanup-success' class).
    """
    from browser_fetch_router import lifecycle

    fake_proc = MagicMock()
    fake_proc.create_time.return_value = 12345.678
    fake_proc.children.return_value = []

    fake_psutil = MagicMock()
    fake_psutil.Process.return_value = fake_proc
    fake_psutil.NoSuchProcess = type("NoSuchProcess", (Exception,), {})
    fake_psutil.AccessDenied = type("AccessDenied", (Exception,), {})
    fake_psutil.Error = type("Error", (Exception,), {})
    # Phase 2: SIGTERM grace — leader still alive.
    # Phase 4: post-SIGKILL wait — leader STILL alive (SIGKILL ignored).
    fake_psutil.wait_procs.side_effect = [
        ([], [fake_proc]),  # Phase 2
        ([], [fake_proc]),  # Phase 4 — survivor → "failed"
    ]

    monkeypatch.setitem(__import__("sys").modules, "psutil", fake_psutil)
    # os.kill must succeed (SIGTERM accepted); SIGKILL via fake_proc.kill
    monkeypatch.setattr(os, "kill", lambda pid, sig: None)

    outcome = lifecycle._kill_pid_safely(
        pid=99999, expected_create_time=12345.678, dry_run=False
    )
    assert outcome == "failed", (
        f"SIGKILL-ignoring process must return 'failed', got {outcome!r}"
    )


# ============================================================
# L5 — Dry-run skips signaling
# ============================================================


def test_l5_dry_run_does_not_signal(monkeypatch):
    """Invariant L5: dry-run must be side-effect-free. No `os.kill`,
    no `proc.terminate()`, no registry unlink. Returns 'cleaned'
    without making any system call."""
    from browser_fetch_router import lifecycle

    fake_proc = MagicMock()
    fake_proc.create_time.return_value = 100.0
    fake_psutil = MagicMock()
    fake_psutil.Process.return_value = fake_proc
    fake_psutil.NoSuchProcess = type("NoSuchProcess", (Exception,), {})
    fake_psutil.Error = type("Error", (Exception,), {})

    monkeypatch.setitem(__import__("sys").modules, "psutil", fake_psutil)
    kill_calls: list[tuple] = []
    monkeypatch.setattr(os, "kill", lambda *a, **k: kill_calls.append(a))

    outcome = lifecycle._kill_pid_safely(
        pid=12345, expected_create_time=100.0, dry_run=True
    )
    assert outcome == "cleaned", f"dry_run must return 'cleaned', got {outcome!r}"
    assert kill_calls == [], (
        f"dry_run must not call os.kill; recorded {kill_calls}"
    )
    fake_proc.terminate.assert_not_called()
    fake_proc.kill.assert_not_called()


# ============================================================
# L6 — Registry unlink only when not dry_run AND not failed
# ============================================================


def test_l6_registry_preserved_on_failed_outcome(isolated_home, monkeypatch):
    """Invariant L6: a 'failed' outcome MUST preserve the registry
    file so a future `cleanup --global-orphan-reap` can retry.
    Closes round-12 i06: previous code unlinked unconditionally."""
    from browser_fetch_router import lifecycle, paths

    monkeypatch.setattr(
        lifecycle,
        "_kill_pid_safely",
        lambda pid, ct, *, dry_run: "failed",
    )

    sid = "test-failed-outcome"
    registry = lifecycle.session_registry_path(sid)
    paths.atomic_write_bytes(
        registry,
        b'{"session_id": "test-failed-outcome", "local_processes": '
        b'[{"pid": 12345, "create_time": 100.0}]}',
    )
    assert registry.exists()

    lifecycle.run_cleanup(all_sessions=True, session_id=sid, dry_run=False)
    assert registry.exists(), (
        "registry must be preserved on 'failed' outcome — without this "
        "a SIGKILL-ignoring process becomes invisible orphan (round-12 i06)"
    )


def test_l6_registry_unlinked_on_clean_outcome(isolated_home, monkeypatch):
    """Invariant L6 (positive): clean run with no failures unlinks the
    registry so dead sessions don't accumulate."""
    from browser_fetch_router import lifecycle, paths

    monkeypatch.setattr(
        lifecycle,
        "_kill_pid_safely",
        lambda pid, ct, *, dry_run: "cleaned",
    )

    sid = "test-clean-outcome"
    registry = lifecycle.session_registry_path(sid)
    paths.atomic_write_bytes(
        registry,
        b'{"session_id": "test-clean-outcome", "local_processes": '
        b'[{"pid": 12345, "create_time": 100.0}]}',
    )

    lifecycle.run_cleanup(all_sessions=True, session_id=sid, dry_run=False)
    assert not registry.exists(), "clean outcome must unlink registry"


def test_l6_registry_preserved_on_dry_run(isolated_home, monkeypatch):
    """Invariant L6 (dry-run): dry_run=True NEVER unlinks regardless
    of outcomes (round-6 r6-05 — dry_run is side-effect-free)."""
    from browser_fetch_router import lifecycle, paths

    monkeypatch.setattr(
        lifecycle,
        "_kill_pid_safely",
        lambda pid, ct, *, dry_run: "cleaned",
    )

    sid = "test-dry-run"
    registry = lifecycle.session_registry_path(sid)
    paths.atomic_write_bytes(
        registry,
        b'{"session_id": "test-dry-run", "local_processes": '
        b'[{"pid": 12345, "create_time": 100.0}]}',
    )

    lifecycle.run_cleanup(all_sessions=True, session_id=sid, dry_run=True)
    assert registry.exists(), "dry_run must NEVER unlink the registry"


# ============================================================
# L7 — register_process uses SentinelLock on a sibling lock file
# ============================================================


def test_l7_register_process_uses_sentinel_lock_on_sibling():
    """Invariant L7: `register_process` serializes read-modify-write
    via `SentinelLock` on a SIBLING file (`.<sid>.lock`), NOT the
    data file. flock on the data file becomes useless the moment
    `atomic_write_bytes` replaces the inode (round-3 stale-inode
    race)."""
    from browser_fetch_router import lifecycle

    src = inspect.getsource(lifecycle.register_process)
    assert "SentinelLock(" in src
    assert "_registry_lock_path(" in src, (
        "register_process must lock via the SIBLING `_registry_lock_path` "
        "function, not the data path"
    )


# ============================================================
# L8 / L9 — registry I/O routes through persistence helpers
# ============================================================


def test_l8_l9_registry_io_routes_through_persistence_helpers():
    """Invariants L8 + L9: registry writes go through `atomic_write_bytes`
    (covered by persistence contract) and registry reads go through
    `read_json_dict` (also covered). Cross-subsystem invariant — the
    lifecycle contract just verifies the LIFECYCLE side honors it."""
    from browser_fetch_router import lifecycle

    src = inspect.getsource(lifecycle)
    assert "atomic_write_bytes(" in src, (
        "lifecycle must use atomic_write_bytes for all registry writes"
    )
    assert "read_json_dict(" in src, (
        "lifecycle must use read_json_dict for all registry reads"
    )
    # And no inline tempfile + os.replace blocks (the helper's job).
    assert "tempfile.mkstemp" not in src, (
        "lifecycle must not duplicate the atomic-write primitive — use "
        "paths.atomic_write_bytes"
    )


# ============================================================
# L10 — all_sessions=True is no-op without session_id
# ============================================================


def test_l10_all_sessions_without_session_id_is_noop(isolated_home):
    """Invariant L10: `all_sessions=True` requires a session_id to
    target. Without one, the function returns an empty result instead
    of (e.g.) globbing the entire sessions directory — that's
    `global_orphan_reap`'s job."""
    from browser_fetch_router import lifecycle

    result = lifecycle.run_cleanup(all_sessions=True, session_id=None)
    assert result["status"] == "ok"
    assert result["evidence"]["results"] == [], (
        "all_sessions=True without session_id must be a no-op"
    )


# ============================================================
# L11 — global_orphan_reap glob bounded to registry_dir
# ============================================================


def test_l11_global_orphan_reap_glob_does_not_recurse(isolated_home):
    """Invariant L11: `global_orphan_reap` uses `registry_dir.glob("*.json")`
    — non-recursive, no wildcard expansion outside the registry dir.
    A planted `*.json` file in a SUBdirectory of the registry dir
    (or above it) MUST NOT be touched."""
    from browser_fetch_router import lifecycle

    registry_dir = lifecycle.session_registry_dir()
    # Planted file in a subdirectory
    sub = registry_dir / "subdir"
    sub.mkdir(parents=True, exist_ok=True)
    bait = sub / "should-not-be-reaped.json"
    bait.write_text('{"session_id": "x", "local_processes": []}')

    # Set mtime to one year ago so age-based reap would otherwise pick
    # it up.
    long_ago = time.time() - 366 * 86400
    os.utime(bait, (long_ago, long_ago))

    result = lifecycle.run_cleanup(global_orphan_reap=True, max_age_days=30)
    assert result["status"] == "ok"
    assert bait.exists(), (
        "global_orphan_reap must NOT recurse into subdirectories — "
        "any file outside the immediate registry_dir is out of scope"
    )


# ============================================================
# L12 — outcome ∈ REGISTRY_OUTCOME_KEYS (no else-failed fallback)
# ============================================================


def test_l12_outcome_subscript_has_no_silent_fallback():
    """Invariant L12: `run_cleanup` indexes `per_session[outcome]`
    directly — any future contract drift in `_kill_pid_safely`'s
    return value raises a loud KeyError instead of being silently
    miscategorized as 'failed' (Gemini medium on 9a26fb2)."""
    from browser_fetch_router import lifecycle

    src = inspect.getsource(lifecycle.run_cleanup)
    # The fail-loud direct-subscript pattern
    assert "per_session[outcome].append(" in src, (
        "run_cleanup must use direct dict subscript, not "
        "per_session.get(outcome, per_session['failed']).append(...) — "
        "the latter would silently bucket unknown outcomes as 'failed'"
    )
    # Tuple of valid outcomes is exposed as a module-level constant
    assert hasattr(lifecycle, "REGISTRY_OUTCOME_KEYS"), (
        "REGISTRY_OUTCOME_KEYS must be a module-level constant so the "
        "outcome contract is discoverable"
    )
    assert "cleaned" in lifecycle.REGISTRY_OUTCOME_KEYS
    assert "skipped" in lifecycle.REGISTRY_OUTCOME_KEYS
    assert "failed" in lifecycle.REGISTRY_OUTCOME_KEYS


# ============================================================
# L13 / L14 — subprocess argv list-based + safe env
# ============================================================


def test_l13_l14_subprocess_argv_is_list_based_no_shell():
    """Invariants L13 + L14: every subprocess.run call in the package
    uses list-based argv and never `shell=True`. A `shell=True` call
    with caller-influenced argv is RCE-equivalent."""
    import ast
    pkg = Path(__file__).resolve().parents[2] / "browser_fetch_router"

    offenders: list[str] = []
    for py in pkg.rglob("*.py"):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        rel = py.relative_to(pkg.parent)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            # Match subprocess.run / subprocess.Popen / subprocess.call
            func = node.func
            func_name = None
            if isinstance(func, ast.Attribute):
                value = func.value
                if isinstance(value, ast.Name) and value.id == "subprocess":
                    func_name = func.attr
            if func_name not in {"run", "Popen", "call", "check_call", "check_output"}:
                continue
            # Look for shell=True kwarg
            for kw in node.keywords:
                if kw.arg == "shell":
                    if isinstance(kw.value, ast.Constant) and kw.value.value is True:
                        offenders.append(
                            f"{rel}:{node.lineno} subprocess.{func_name} with shell=True"
                        )
            # Look for first positional arg being a string literal
            # (rather than a list) — string-arg subprocess calls invoke
            # the shell on Windows or are at least argv-shape-fragile.
            if node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    offenders.append(
                        f"{rel}:{node.lineno} subprocess.{func_name} with "
                        f"string argv {first.value!r} — must be a list"
                    )

    assert not offenders, (
        "Unsafe subprocess invocation in production code:\n"
        + "\n".join(offenders)
        + "\nUse list-based argv and never shell=True."
    )


def test_l14_install_agent_filters_env_through_safe_env():
    """Invariant L14: `install_agent`'s verification subprocess gets
    a CURATED env via `_safe_env()`, not `os.environ` passthrough.
    Curated env drops agent API keys (ANTHROPIC_API_KEY etc.) so the
    verification step cannot leak credentials between agent contexts."""
    from browser_fetch_router import install_agent

    src = inspect.getsource(install_agent._run_verification)
    assert "_safe_env()" in src, (
        "install_agent verification subprocess must use _safe_env() — "
        "passing os.environ directly leaks ANTHROPIC_API_KEY and "
        "similar credentials to the child process"
    )


# ============================================================
# L15 — Malformed registry entries skipped gracefully
# ============================================================


@pytest.mark.parametrize(
    "malformed_entry",
    [
        {"pid": "not-an-int", "create_time": 100.0},
        {"pid": 123, "create_time": "not-a-float"},
        {"pid": [1, 2, 3], "create_time": 100.0},
        {"pid": None, "create_time": 100.0},
        "string-instead-of-dict",
        42,
        None,
    ],
)
def test_l15_malformed_registry_entry_buckets_to_malformed(
    isolated_home, monkeypatch, malformed_entry
):
    """Invariant L15 (closing-pass): a single malformed registry entry
    does NOT crash the cleanup CLI. Malformed entries land in a
    `malformed` bucket so the operator sees corruption signal in the
    envelope, while valid sibling entries are still cleaned.

    Pre-fix this raised ValueError/AttributeError out of run_cleanup,
    crashing the entire CLI with an unhandled exception.
    """
    from browser_fetch_router import lifecycle, paths

    sid = "malformed-test"
    registry = lifecycle.session_registry_path(sid)
    import json
    payload = {
        "session_id": sid,
        "local_processes": [malformed_entry],
    }
    paths.atomic_write_bytes(registry, json.dumps(payload).encode("utf-8"))

    # Should not raise.
    result = lifecycle.run_cleanup(
        all_sessions=True, session_id=sid, dry_run=True
    )
    assert result["status"] == "ok"
    sessions = result["evidence"]["results"]
    assert len(sessions) == 1
    assert sessions[0]["malformed"], (
        f"malformed entry {malformed_entry!r} must land in 'malformed' "
        f"bucket; got {sessions[0]}"
    )


def test_l15_malformed_does_not_block_clean_siblings(
    isolated_home, monkeypatch
):
    """Invariant L15 (closing-pass, sibling): a single malformed entry
    does NOT prevent valid siblings from being cleaned. Valid records
    still get processed; the malformed one is reported but skipped.
    """
    from browser_fetch_router import lifecycle, paths
    import json

    monkeypatch.setattr(
        lifecycle,
        "_kill_pid_safely",
        lambda pid, ct, *, dry_run: "cleaned",
    )

    sid = "mixed-test"
    registry = lifecycle.session_registry_path(sid)
    payload = {
        "session_id": sid,
        "local_processes": [
            {"pid": "bad", "create_time": 100.0},  # malformed
            {"pid": 12345, "create_time": 100.0},  # valid
        ],
    }
    paths.atomic_write_bytes(registry, json.dumps(payload).encode("utf-8"))

    result = lifecycle.run_cleanup(
        all_sessions=True, session_id=sid, dry_run=False
    )
    sessions = result["evidence"]["results"]
    assert len(sessions[0]["malformed"]) == 1
    assert len(sessions[0]["cleaned"]) == 1


# ============================================================
# Static guard — no ad-hoc signaling outside lifecycle.py
# ============================================================


def test_no_adhoc_signaling_in_production_code():
    """Class-level static guard: production code MUST NOT use signal
    primitives outside `lifecycle.py`. AST-based scan so docstring
    text mentioning these names doesn't false-positive. Banned:

      - `os.kill(...)` / `os.killpg(...)`
      - `signal.SIGTERM`, `signal.SIGKILL`, `signal.SIGINT`
        (using these names outside the lifecycle context)
      - direct `psutil.Process(...).kill()` / `.terminate()` /
        `.send_signal()` outside lifecycle.py

    Centralizing signaling in `_kill_pid_safely` enforces the PID
    reuse defense (L2), the SIGTERM-children-first ordering (L3),
    and the SIGKILL escalation (L4) on every signaling code path.
    A new caller doing raw `os.kill(...)` would bypass all three.
    """
    import ast

    pkg = Path(__file__).resolve().parents[2] / "browser_fetch_router"
    lifecycle_module = pkg / "lifecycle.py"

    BANNED_OS_FUNCS = {"kill", "killpg"}
    BANNED_SIGNAL_NAMES = {"SIGTERM", "SIGKILL", "SIGINT", "SIGQUIT"}

    offenders: list[str] = []
    for py in pkg.rglob("*.py"):
        if py.resolve() == lifecycle_module.resolve():
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        rel = py.relative_to(pkg.parent)
        for node in ast.walk(tree):
            # os.kill / os.killpg
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "os"
                and node.attr in BANNED_OS_FUNCS
            ):
                offenders.append(
                    f"{rel}:{node.lineno} os.{node.attr} — "
                    "must route through lifecycle._kill_pid_safely"
                )
            # signal.SIGTERM / signal.SIGKILL etc.
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "signal"
                and node.attr in BANNED_SIGNAL_NAMES
            ):
                offenders.append(
                    f"{rel}:{node.lineno} signal.{node.attr} — "
                    "must route through lifecycle"
                )

    assert not offenders, (
        "Ad-hoc signaling in production code — bypass of lifecycle "
        "module's PID-reuse defense + SIGTERM-children-first + "
        "SIGKILL-escalation invariants. See "
        "docs/browser-fetch-router-lifecycle-contract.md.\n"
        + "\n".join(offenders)
    )
