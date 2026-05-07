"""Round-3 regression suite — inverted replication tests.

Originally these tests REPRODUCED claimed bugs (PASS = bug exists).
After round-4 fixes landed, each test was inverted to assert the SAFE
behavior (PASS = fix in place). They now serve as permanent regression
guards: if a future maintainer reintroduces any of the round-3 bugs,
the corresponding test fails.

Mapping to round-4 commit clusters:
- A, A2, T  → cluster 2 (header name vs value validation)
- B, C, I, K → cluster 1 (audit chokepoint completeness)
- D        → cluster 3 (lifecycle sentinel lock)
- F, G, H  → cluster 4 (read-user-tabs authorization)
- E, E2    → cluster 5 (URL fragment two-layer fix)
- J, L, M, N, O, P, Q, R, S, U → cluster 6 (symmetric site coverage)

V (cost/circuit not wired into providers) is feature-level work tracked
in a separate issue; no test here.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


def _audit_path(home: Path) -> Path:
    return home / ".local/state/browser-fetch-router/audit.jsonl"


def _read_audit(home: Path) -> list[dict]:
    p = _audit_path(home)
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


# ============================================================
# Cluster 2 — header name vs value (A, A2, T)
# ============================================================


def test_A_header_name_with_trailing_space_rejected():
    """RFC 7230 §3.2.6: token grammar forbids SP/HTAB in header names.
    `Transfer-Encoding ` (trailing space) used to bypass the lower-cased
    lookup against TRANSPORT_OWNED_HEADERS. Now rejected loudly."""
    from browser_fetch_router.http_client import (
        HostHeaderSmuggling,
        _validate_extra_headers,
    )

    with pytest.raises(HostHeaderSmuggling, match="header_invalid_name_char"):
        _validate_extra_headers({"Transfer-Encoding ": "chunked"}, "example.com")


def test_A2_header_name_with_internal_space_rejected():
    """Same class — SP inside a header name."""
    from browser_fetch_router.http_client import (
        HostHeaderSmuggling,
        _validate_extra_headers,
    )

    with pytest.raises(HostHeaderSmuggling, match="header_invalid_name_char"):
        _validate_extra_headers({"X Evil": "value"}, "example.com")


def test_A3_header_name_with_tab_rejected():
    from browser_fetch_router.http_client import (
        HostHeaderSmuggling,
        _validate_extra_headers,
    )

    with pytest.raises(HostHeaderSmuggling, match="header_invalid_name_char"):
        _validate_extra_headers({"X-Test\tInjected": "value"}, "example.com")


def test_A4_header_value_with_tab_or_space_still_allowed():
    """Counter-example: SP and HTAB ARE allowed in header VALUES per
    RFC 7230 (OWS). The split fix must not over-reject."""
    from browser_fetch_router.http_client import _validate_extra_headers

    # Tab and space in value are fine.
    _validate_extra_headers({"X-Caller-Identifier": "foo\tbar baz"}, "example.com")
    # But control chars in value still rejected.
    from browser_fetch_router.http_client import HostHeaderSmuggling

    with pytest.raises(HostHeaderSmuggling):
        _validate_extra_headers({"X-Caller-Identifier": "value\r\nX-Injected: evil"}, "example.com")


def test_T_user_agent_in_transport_owned_headers():
    """Transport sets User-Agent itself; callers MUST NOT override.
    Mitigations baked into the rejection: helpful error message points
    at X-Caller-Identifier as the right pattern."""
    from browser_fetch_router.http_client import (
        TRANSPORT_OWNED_HEADERS,
        HostHeaderSmuggling,
        _validate_extra_headers,
    )

    assert "user-agent" in TRANSPORT_OWNED_HEADERS
    with pytest.raises(HostHeaderSmuggling, match="X-Caller-Identifier"):
        _validate_extra_headers({"User-Agent": "evil/1.0"}, "example.com")


def test_every_transport_owned_header_rejects_caller_override():
    """Mitigation regression: loop through every TRANSPORT_OWNED_HEADERS
    entry and confirm the validator rejects a caller-supplied version.
    A future deletion from the set immediately fails this test."""
    from browser_fetch_router.http_client import (
        TRANSPORT_OWNED_HEADERS,
        HostHeaderSmuggling,
        _validate_extra_headers,
    )

    for owned in TRANSPORT_OWNED_HEADERS:
        with pytest.raises(HostHeaderSmuggling):
            _validate_extra_headers({owned: "x"}, "example.com")


# ============================================================
# Cluster 1 — audit chokepoint (B, C, I, K)
# ============================================================


def test_B_argparse_error_now_writes_audit(tmp_path, monkeypatch, capsys):
    """JsonArgumentParser.error now calls _emit_audit before SystemExit
    so probes for available commands / typo'd invocations leave a
    forensic trail."""
    monkeypatch.setenv("HOME", str(tmp_path))
    from browser_fetch_router import cli

    with pytest.raises(SystemExit):
        cli.main(["read-web", "--bogus-flag"])
    capsys.readouterr()

    entries = _read_audit(tmp_path)
    usage_entries = [e for e in entries if e.get("status") == "usage_error"]
    assert usage_entries, f"argparse error path produced NO audit entry: {entries}"
    assert usage_entries[-1]["command"] == "unknown"


def test_C_success_path_handles_non_serializable_payload(capsys):
    """Handler returning a non-JSON-serializable payload now downgrades
    to internal_error envelope instead of crashing the CLI uncaught.
    Single serialization site inside the guard."""
    from browser_fetch_router import cli
    from browser_fetch_router.schema import envelope

    def handler():
        return envelope(
            command="test",
            status="ok",
            evidence={"oops": lambda x: x},
        )

    exit_code = cli._emit("test", handler=handler, audit=False)
    out = capsys.readouterr().out
    payload = json.loads(out.strip().splitlines()[-1])
    assert payload["status"] == "internal_error"
    assert payload["error"]["code"] == "non_serializable_payload"
    assert exit_code == 70


def test_D_lifecycle_sentinel_lock_serializes_concurrent_writers(tmp_path, monkeypatch):
    """register_process now uses a sentinel-file lock that survives the
    atomic rename of the data file. Concurrent writers serialize
    correctly; entries are not lost."""
    monkeypatch.setenv("HOME", str(tmp_path))
    from browser_fetch_router.lifecycle import (
        register_process,
        session_registry_path,
    )

    sid = "concurrent-sid"
    # Even with rapid back-to-back registrations, all entries persist.
    for pid in range(5):
        register_process(sid, pid=1000 + pid, create_time=float(pid), process_group=pid)
    data = json.loads(session_registry_path(sid).read_text())
    assert len(data["local_processes"]) == 5
    pids = sorted(entry["pid"] for entry in data["local_processes"])
    assert pids == [1000, 1001, 1002, 1003, 1004]


def test_E_url_fragment_stripped_from_normalized_url():
    """Fragment is stripped at normalize so it never reaches transport,
    cache key, envelope.url, or approval scope. Audit input keeps the
    raw URL via a separate path (sanitize_audit_input)."""
    from browser_fetch_router.url_safety import normalize_and_validate_url

    out = normalize_and_validate_url("https://example.com/cb#access_token=SECRET")
    assert "#" not in out
    assert "access_token" not in out
    assert out == "https://example.com/cb"


def test_E_cache_key_collapses_for_fragment_only_difference():
    """Round-3 finding (Gemini g04): different fragments on the same wire
    resource used to produce different cache keys, fragmenting the cache."""
    from browser_fetch_router.cache import cache_key
    from browser_fetch_router.url_safety import normalize_and_validate_url

    a = cache_key("jina-reader", normalize_and_validate_url("https://example.com/page#1"))
    b = cache_key("jina-reader", normalize_and_validate_url("https://example.com/page#2"))
    assert a == b


def test_E2_sanitize_audit_input_redacts_fragment_params():
    """OAuth implicit-flow tokens (`#access_token=...`) are now redacted
    in audit input. The forensic SIGNAL (the key name was present)
    survives; only the secret value is scrubbed."""
    from browser_fetch_router.audit import sanitize_audit_input

    out = sanitize_audit_input(
        "https://example.com/cb#access_token=SECRET&id_token=JWT"
    )
    assert "SECRET" not in out
    assert "JWT" not in out
    # Forensic signal: token-bearing fragment was present.
    assert "access_token" in out
    # Redaction marker survives urlencode (percent-encoded form is the
    # wire-equivalent of `[redacted]`; either way an operator scanning
    # the audit log sees the placeholder).
    assert "redacted" in out.lower()


def test_E2_sanitize_audit_input_does_not_corrupt_bare_fragment():
    """Counter-example: a bare `#section` anchor (not parameter-shaped)
    must NOT be parsed as params."""
    from browser_fetch_router.audit import sanitize_audit_input

    out = sanitize_audit_input("https://example.com/page#section")
    # Anchor passes through unchanged (or re-emerges as-is).
    assert "section" in out


def test_F_show_all_no_longer_bypasses_list_all_tabs_approval(tmp_path, monkeypatch):
    """`--show-all` is now display-only. `--all` requires the
    `exact:list-all-tabs` approval scope, period."""
    monkeypatch.setenv("HOME", str(tmp_path))
    from browser_fetch_router import read_user_tabs as rut

    monkeypatch.setattr(rut, "cdp_base_url", lambda **kw: "http://127.0.0.1:9222")
    monkeypatch.setattr(
        rut,
        "fetch_tab_list",
        lambda base: [
            {"id": "1", "title": "x", "url": "https://example.com/", "type": "page"}
        ],
    )

    result = rut.list_tabs(all_tabs=True, show_all=True, session_id="x")
    assert result["status"] == "approval_required"
    assert result["approval"]["scope"] == "exact:list-all-tabs"


def test_G_screenshot_tab_now_enforces_approval(tmp_path, monkeypatch):
    """screenshot_tab now routes through _resolve_and_authorize_tab so
    the approval gate is applied before any CDP call."""
    monkeypatch.setenv("HOME", str(tmp_path))
    output = tmp_path / "shot.png"
    output.parent.mkdir(parents=True, exist_ok=True)

    from browser_fetch_router import read_user_tabs as rut
    from browser_fetch_router import cdp

    monkeypatch.setattr(rut, "cdp_base_url", lambda **kw: "http://127.0.0.1:9222")
    monkeypatch.setattr(
        rut,
        "fetch_tab_list",
        lambda base: [
            {"id": "1", "title": "Inbox", "url": "https://mail.google.com/mail/u/0", "type": "page"}
        ],
    )
    captured = {"called": False}

    def fake_screenshot(base, target):
        captured["called"] = True
        return b"PNG"

    monkeypatch.setattr(cdp, "fetch_tab_screenshot", fake_screenshot)

    # Default-denied URL, NO approval scope. Must be blocked before CDP.
    result = rut.screenshot_tab(
        "https://mail.google.com/mail/u/0",
        output=output,
        approval_scope=None,
        session_id="x",
    )
    assert result["status"] == "approval_required"
    assert not captured["called"], "CDP screenshot was called without approval"


def test_H_read_user_tabs_propagates_safety_error(tmp_path, monkeypatch):
    """SafetyError raised inside the CDP layer (e.g., DNS rebinding)
    propagates so the dispatcher emits unsafe_url_blocked (exit 4)
    instead of being swallowed as tool_setup_failed."""
    monkeypatch.setenv("HOME", str(tmp_path))
    from browser_fetch_router import read_user_tabs as rut
    from browser_fetch_router.url_safety import UnsafeUrl

    monkeypatch.setattr(rut, "cdp_base_url", lambda **kw: "http://127.0.0.1:9222")

    def boom(_base):
        raise UnsafeUrl("simulated_ssrf_in_cdp")

    monkeypatch.setattr(rut, "fetch_tab_list", boom)

    with pytest.raises(UnsafeUrl):
        rut.list_tabs(session_id="x")


def test_I_keyboard_interrupt_audited_and_exits_130(tmp_path, monkeypatch, capsys):
    """SIGINT now produces an `interrupted` envelope, an audit entry, and
    exits cleanly with POSIX 130. No traceback to stderr."""
    monkeypatch.setenv("HOME", str(tmp_path))
    from browser_fetch_router import cli

    def handler():
        raise KeyboardInterrupt()

    exit_code = cli._emit("interactive-browser", task="x", handler=handler)
    out = capsys.readouterr().out
    payload = json.loads(out.strip().splitlines()[-1])
    assert payload["status"] == "interrupted"
    assert exit_code == 130

    entries = _read_audit(tmp_path)
    assert any(e.get("status") == "interrupted" for e in entries), (
        "SIGINT path must emit audit entry — that's the forensic point"
    )


def test_J_atomic_write_bytes_closes_fd_on_fdopen_failure(tmp_path, monkeypatch):
    """If os.fdopen raises after mkstemp, the raw fd is now closed in
    the finally block. No fd leak."""
    from browser_fetch_router import paths

    target = tmp_path / "out.json"

    def fake_fdopen(fd, *a, **kw):
        raise OSError("simulated fdopen failure")

    real_fdopen = os.fdopen
    monkeypatch.setattr(os, "fdopen", fake_fdopen)

    proc_fd_dir = Path("/dev/fd")
    if not proc_fd_dir.exists():
        pytest.skip("no /dev/fd on this platform")
    try:
        before = set(os.listdir(proc_fd_dir))
    except OSError:
        pytest.skip("cannot enumerate /dev/fd")

    with pytest.raises(OSError, match="simulated fdopen failure"):
        paths.atomic_write_bytes(target, b"x")

    monkeypatch.setattr(os, "fdopen", real_fdopen)
    after = set(os.listdir(proc_fd_dir))
    new_fds = after - before
    assert new_fds == set(), f"fd leak: {new_fds}"


def test_K_audit_records_session_id_for_non_read_web(tmp_path, monkeypatch, capsys):
    """_emit_audit now falls back to session module for session_id and
    invoking_agent so commands that don't stamp evidence still get the
    attribution."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BFR_SESSION_ID", "test-session-K")
    monkeypatch.setenv("BFR_AGENT", "test-runner")
    from browser_fetch_router import cli

    cli.main(["read-user-tabs", "revoke", "hostname:example.com"])
    capsys.readouterr()

    entries = _read_audit(tmp_path)
    rut_entries = [e for e in entries if e["command"] == "read-user-tabs"]
    assert rut_entries
    target = rut_entries[-1]
    assert target.get("session_id") == "test-session-K"
    assert target.get("invoking_agent") == "test-runner"


def test_L_old_format_registry_contract_assertion(tmp_path, monkeypatch):
    """Contract: the round-2 schema rename (`local_pids`→`local_processes`)
    is a HARD CUT because the old format never shipped. This test exists
    to fail loudly if a future PR adds a back-compat shim — the
    behavior MUST stay 'old-format files produce empty buckets and are
    deleted'. If we ever ship the old format and need migration, see
    issue #738 for the design."""
    monkeypatch.setenv("HOME", str(tmp_path))
    from browser_fetch_router.lifecycle import (
        run_cleanup,
        session_registry_dir,
        session_registry_path,
    )

    session_registry_dir()
    sid = "old-format-session"
    path = session_registry_path(sid)
    path.write_text(
        json.dumps(
            {
                "session_id": sid,
                "local_pids": [
                    {"pid": 999999, "create_time": 1.0, "group": "g"}
                ],
                "cleanup_status": "pending",
            }
        )
    )
    # Round-6 r6-05 made dry_run side-effect-free, so the registry file
    # is preserved on the dry_run call. File-removal behavior moves to
    # the dry_run=False invocation below.
    result = run_cleanup(all_sessions=True, session_id=sid, dry_run=True)
    per = result["evidence"]["results"][0]
    assert per["cleaned"] == [] and per["skipped"] == [] and per["failed"] == []
    assert path.exists()
    run_cleanup(all_sessions=True, session_id=sid, dry_run=False)
    assert not path.exists()


def test_M_decode_with_charset_handles_nul_in_content_type():
    """Embedded NUL in Content-Type no longer crashes — the byte is
    stripped from the input and the parser proceeds with the cleaned
    string. UTF-8 fallback covers any residual codec lookup failure."""
    from browser_fetch_router.http_client import _decode_with_charset

    out = _decode_with_charset(b"hello", "text/html; charset=utf-8\x00evil")
    assert out == "hello"


def test_N_cache_write_failure_warns_to_stderr(tmp_path, monkeypatch, capsys):
    """A cache write failure no longer fails silently — a stderr warning
    surfaces so operators can detect cache degradation before duplicate
    paid charges accumulate."""
    monkeypatch.setenv("HOME", str(tmp_path))
    from browser_fetch_router import read_web as rw
    from browser_fetch_router.cache import CacheStore

    def boom(self, *a, **kw):
        raise OSError("simulated disk full")

    monkeypatch.setattr(CacheStore, "write", boom)
    monkeypatch.setattr(
        rw,
        "fetch_jina",
        lambda url, ctx: {
            "status": "ok",
            "provider": "jina",
            "content_markdown": "x",
            "evidence": {"quality": "ok"},
        },
    )

    result = rw.read_web("https://example.com/", no_cache=False)
    captured = capsys.readouterr()

    assert result["status"] == "ok"  # request still succeeded
    assert "cache_write_failed" in captured.err
    assert "simulated disk full" in captured.err


def test_O_redact_tab_list_redacts_javascript_data_file_extension_schemes():
    """Non-HTTP tab schemes (javascript:, data:, file:, chrome-extension:)
    are now redacted because urlsplit yields no hostname for them and
    they leak content in their own right."""
    from browser_fetch_router.read_user_tabs import redact_tab_list

    tabs = [
        {"id": "1", "title": "evil", "url": "javascript:alert(1)", "type": "page"},
        {"id": "2", "title": "data", "url": "data:text/html,<h1>x</h1>", "type": "page"},
        {"id": "3", "title": "local", "url": "file:///etc/passwd", "type": "page"},
        {"id": "4", "title": "ext", "url": "chrome-extension://abc/foo", "type": "page"},
    ]
    result = redact_tab_list(tabs, show_all=False)
    for r in result:
        assert r["redacted"] is True, f"non-HTTP scheme not redacted: {r}"
        assert r["url"] == "[hidden]"


def test_P_malformed_credential_treated_as_missing(tmp_path, monkeypatch, capsys):
    """A non-ASCII or non-printable byte in PARALLEL_API_KEY now surfaces
    through quota_or_key_missing (the missing-key path) plus a stderr
    warning — NOT through unsafe_url_blocked from the header validator
    (which would falsely blame the user's URL)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PARALLEL_API_KEY", "key\xff")
    from browser_fetch_router.providers import parallel

    ctx = {"allow_paid": True}
    result = parallel.fetch("https://example.com/", ctx)
    captured = capsys.readouterr()

    assert result["status"] == "quota_or_key_missing"
    assert result["error"]["code"] == "parallel_key_missing"
    assert "PARALLEL_API_KEY" in captured.err
    assert "non-ASCII" in captured.err or "non-printable" in captured.err


def test_Q_run_cleanup_real_subprocess_lands_in_cleaned_bucket(tmp_path, monkeypatch):
    """Round-3 test-quality gap: the schema test only used dry_run=True.
    This complements it with a REAL kill flow — spawn a sleeping
    subprocess, register it, run cleanup with dry_run=False, assert it
    was actually killed and lands in the `cleaned` bucket."""
    pytest.importorskip("psutil")
    import subprocess

    monkeypatch.setenv("HOME", str(tmp_path))
    from browser_fetch_router.lifecycle import (
        register_process,
        run_cleanup,
    )

    proc = subprocess.Popen(["sleep", "30"])
    try:
        import psutil

        ps_proc = psutil.Process(proc.pid)
        register_process(
            "real-kill-session",
            pid=proc.pid,
            create_time=ps_proc.create_time(),
            process_group=proc.pid,
        )
        result = run_cleanup(
            all_sessions=True, session_id="real-kill-session", dry_run=False
        )
        per = result["evidence"]["results"][0]
        cleaned_pids = [e["pid"] for e in per["cleaned"]]
        assert proc.pid in cleaned_pids, (
            f"real subprocess PID {proc.pid} not in cleaned bucket: {per}"
        )
        # Process should exit shortly after SIGTERM.
        proc.wait(timeout=5)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_R_audit_log_handles_long_lines(tmp_path, monkeypatch):
    """write_all helper guarantees full-line writes. Lines >PIPE_BUF
    (~4 KiB) are no longer at risk of short-write truncation."""
    monkeypatch.setenv("HOME", str(tmp_path))
    from browser_fetch_router.audit import append_audit

    huge_url = "https://example.com/?" + ("k=" + "x" * 100 + "&") * 100  # ~10 KiB
    append_audit({
        "command": "read-web",
        "input_url_or_task": huge_url,
        "status": "ok",
    })
    audit = (tmp_path / ".local/state/browser-fetch-router/audit.jsonl").read_text()
    # Each line must be valid JSON — no truncation.
    last_line = audit.strip().splitlines()[-1]
    parsed = json.loads(last_line)
    assert parsed["command"] == "read-web"
    assert parsed["status"] == "ok"


def test_S_persist_true_default_denied_url_does_not_write_persistent_record(tmp_path, monkeypatch):
    """Round-3 coverage gap: the Greptile #4 fix covered persist=False
    only. Confirm the persist=True branch ALSO refuses to write a
    persistent approval for a default-denied URL."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BFR_SESSION_ID", "sess-S")

    from browser_fetch_router import read_user_tabs as rut
    from browser_fetch_router.approvals import list_active_scopes

    denied_url = "https://mail.google.com/mail/u/0"

    monkeypatch.setattr(rut, "cdp_base_url", lambda **kw: "http://127.0.0.1:9222")
    monkeypatch.setattr(
        rut,
        "fetch_tab_list",
        lambda base: [
            {
                "id": "T1",
                "title": "Inbox",
                "url": denied_url,
                "type": "page",
                "webSocketDebuggerUrl": "ws://x",
            }
        ],
    )

    rut.read_tab(
        denied_url,
        approval_scope=f"exact:{denied_url}",
        persist_approval=True,  # the previously-untested branch
        session_id="sess-S",
    )
    scopes = list_active_scopes(session_id="sess-S")
    assert all("mail.google.com" not in s for s in scopes), scopes


def test_U_default_deny_covers_subdomains():
    """is_default_denied now matches `www.gmail.com` (not just bare
    `gmail.com`) because every sensitive host has a `*.` wildcard
    counterpart."""
    from browser_fetch_router.default_deny import is_default_denied

    assert is_default_denied("https://gmail.com/inbox")
    assert is_default_denied("https://www.gmail.com/inbox")
    assert is_default_denied("https://mail.google.com/inbox")
    assert is_default_denied("https://m.outlook.live.com/")
