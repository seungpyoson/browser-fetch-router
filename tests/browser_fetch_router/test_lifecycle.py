from browser_fetch_router.lifecycle import session_registry_dir, session_registry_path


def test_session_registry_lives_in_state(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    path = session_registry_path("session-1")
    assert ".local/state/browser-fetch-router/sessions/session-1.json" in str(path)


def test_session_registry_dir_is_private(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    directory = session_registry_dir()
    mode = directory.stat().st_mode & 0o777
    assert mode == 0o700


# --- Task 15: cleanup verification + action tiers ---------------------------


def test_cleanup_verifies_pid_create_time_before_kill():
    from browser_fetch_router.lifecycle import should_kill_process

    assert should_kill_process(
        expected_pid=123,
        expected_create_time=100.0,
        observed_pid=123,
        observed_create_time=100.0,
    )
    # Different create_time → PID was reused, must not kill.
    assert not should_kill_process(
        expected_pid=123,
        expected_create_time=100.0,
        observed_pid=123,
        observed_create_time=200.0,
    )
    # Different PID → wrong process entirely.
    assert not should_kill_process(
        expected_pid=123,
        expected_create_time=100.0,
        observed_pid=124,
        observed_create_time=100.0,
    )


def test_tier_c_noninteractive_returns_approval_required():
    from browser_fetch_router.interactive import classify_action, require_action_confirmation

    tier = classify_action("click Buy now")
    assert tier == "C"
    result = require_action_confirmation(tier, stdin_is_tty=False, confirmation=None)
    assert result["status"] == "approval_required"


def test_tier_a_proceeds_without_confirmation():
    from browser_fetch_router.interactive import classify_action, require_action_confirmation

    tier = classify_action("read the page")
    assert tier == "A"
    assert require_action_confirmation(tier, stdin_is_tty=False, confirmation=None)["status"] == "ok"


def test_tier_c_with_confirm_proceeds():
    from browser_fetch_router.interactive import require_action_confirmation

    assert require_action_confirmation("C", stdin_is_tty=False, confirmation="confirm-me")["status"] == "ok"


def test_unknown_task_defaults_to_tier_c():
    from browser_fetch_router.interactive import classify_action

    assert classify_action("xyzqq") == "C"


def test_run_cleanup_returns_ok_envelope(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from browser_fetch_router.lifecycle import run_cleanup

    payload = run_cleanup(all_sessions=True, session_id="bfr-test")
    assert payload["status"] == "ok"
    assert payload["evidence"]["session_scope"] == "bfr-test"


# --- External-review (Gemini #4): _read_fd_to_eof loops past short reads ---


def test_read_fd_to_eof_returns_full_content_above_one_chunk(tmp_path):
    """Regression for Gemini #4: `os.read(fd, n)` is allowed to return fewer
    than `n` bytes (POSIX permits short reads). The previous one-shot
    `os.read(fd, 1MB)` silently truncated registry files >1MB and could
    return less than the requested count even on small files. The fix
    loops until EOF."""
    import os

    from browser_fetch_router.lifecycle import _read_fd_to_eof

    # Build a >1MB payload so the previous single-shot read would have
    # truncated.
    payload = (b"x" * 200_000) + (b"y" * 1_500_000)
    target = tmp_path / "big.json"
    target.write_bytes(payload)
    fd = os.open(target, os.O_RDONLY)
    try:
        out = _read_fd_to_eof(fd)
    finally:
        os.close(fd)
    assert out == payload
    assert len(out) == len(payload)


def test_register_process_does_not_lose_history_for_large_registry(tmp_path, monkeypatch):
    """End-to-end: appending a process to an existing >1MB registry file
    must not lose prior entries (would happen if read silently truncated)."""
    import json

    from browser_fetch_router.lifecycle import (
        register_process,
        session_registry_path,
    )

    monkeypatch.setenv("HOME", str(tmp_path))
    sid = "bfr-large"
    path = session_registry_path(sid)
    path.parent.mkdir(parents=True, exist_ok=True)

    pre_existing_pids = [
        {"pid": i, "create_time": float(i), "process_group": "g", "registered_at": 0.0, "info": {}}
        for i in range(1000)
    ]
    payload = {
        "session_id": sid,
        "local_processes": pre_existing_pids,
        "padding": "x" * 1_500_000,  # force file >1MB
    }
    path.write_text(json.dumps(payload))

    register_process(sid, pid=99999, create_time=99999.0, process_group="g")

    after = json.loads(path.read_text())
    # All 1000 prior entries plus the new one — none lost to truncation.
    assert len(after["local_processes"]) == 1001
    assert after["local_processes"][-1]["pid"] == 99999


# --- External-review (Gemini round 2): registry schema contract -----------


def test_register_process_uses_spec_schema_keys(tmp_path, monkeypatch):
    """Regression for Gemini #M4. The registry schema is the contract
    between writer (register_process) and reader (run_cleanup, plus any
    external reaper or doctor probe). The implementation spec mandates
    `local_processes` and `process_group`; the previous code wrote
    `local_pids` and `group`, silently breaking that contract.
    """
    import json

    from browser_fetch_router.lifecycle import (
        REGISTRY_PROCESS_GROUP_KEY,
        REGISTRY_PROCESSES_KEY,
        register_process,
        session_registry_path,
    )

    assert REGISTRY_PROCESSES_KEY == "local_processes"
    assert REGISTRY_PROCESS_GROUP_KEY == "process_group"

    monkeypatch.setenv("HOME", str(tmp_path))
    register_process(
        "bfr-schema",
        pid=12345,
        create_time=1700000000.0,
        process_group=12345,
        info={"command_hint": "browser_use.skill_cli"},
    )
    data = json.loads(session_registry_path("bfr-schema").read_text())
    assert "local_processes" in data
    # Old keys must NOT appear.
    assert "local_pids" not in data
    entry = data["local_processes"][0]
    assert entry["process_group"] == 12345
    assert "group" not in entry


def test_run_cleanup_buckets_outcomes_under_correct_keys(tmp_path, monkeypatch):
    """Regression for Gemini #M5. `_kill_pid_safely` returns one of
    {cleaned, skipped, failed}. The previous run_cleanup initialized the
    per_session dict with `killed` (never populated) and accumulated the
    real outcomes via setdefault, producing JSON like:
        {"killed": [], "skipped": [], "failed": [], "cleaned": [...]}
    The fix initializes the dict with the keys the kill helper actually
    emits and drops the dead `killed` bucket.
    """
    import json

    from browser_fetch_router.lifecycle import (
        REGISTRY_OUTCOME_KEYS,
        register_process,
        run_cleanup,
        session_registry_path,
    )

    assert REGISTRY_OUTCOME_KEYS == ("cleaned", "skipped", "failed")

    monkeypatch.setenv("HOME", str(tmp_path))
    sid = "bfr-cleanup"
    register_process(sid, pid=999999, create_time=1.0, process_group=999999)
    # Sanity: registry exists with our entry.
    assert json.loads(session_registry_path(sid).read_text())["local_processes"]

    payload = run_cleanup(all_sessions=True, session_id=sid, dry_run=True)
    results = payload["evidence"]["results"]
    assert results, "cleanup must return at least one per_session result"
    per_session = results[0]
    # Spec keys present.
    for key in REGISTRY_OUTCOME_KEYS:
        assert key in per_session, f"missing bucket {key!r} in {per_session!r}"
    # Dead key absent.
    assert "killed" not in per_session
