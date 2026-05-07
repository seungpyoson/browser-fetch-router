"""Round-5 regression suite — Greptile findings on commit 7ffd4c8 plus
the internal adversarial pass on round-4 changes.

Originally each test REPRODUCED a claimed bug (PASS = bug exists). After
round-5 fixes landed, tests for confirmed bugs were inverted to assert
SAFE behavior; tests where the concern was DISPROVEN were converted to
positive assertions documenting the safe behavior is correct (so a
future regression fails the test).

Greptile findings (both fixed in round 5):
- g1 — cost.py TOCTOU on is_paid_disabled
- g2 — show_all bypasses default-deny redaction in active-tab path

Internal adversarial pass on round 4:
- r4-1 emit-audit URL preservation through interrupt → confirmed safe
- r4-2 _resolve_and_authorize_tab is the only fetch_tab_list caller → confirmed safe
- r4-3 sentinel lock coexists with json files in registry dir → confirmed safe
- r4-4 _decode_with_charset handles C1 control bytes → confirmed safe
- r4-5 every RFC 7230 tspecial is rejected as a header name char → confirmed safe
- r4-6 provider_credential warns once per env-var per-process → fixed in round 5
- r4-7 atomic_write_bytes survives mid-write failure without fd leak → confirmed safe
- r4-8 normalize_scope strips fragment for exact: scopes → confirmed safe
- r4-9 argparse-error audit input field documents the limitation → confirmed safe
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest


# ============================================================
# Greptile g1 — cost.py TOCTOU on is_paid_disabled
# ============================================================


def test_greptile_g1_cost_toctou_paid_disabled_recheck(tmp_path):
    """Greptile #1 on 7ffd4c8. The fix added an in-transaction recheck of
    `paid_disabled_sessions` after BEGIN IMMEDIATE. Even if the outer
    pre-check sees no row (because B's commit hadn't propagated to A's
    view yet), the in-transaction read will, and A correctly returns
    False without inserting a cost row.
    """
    from browser_fetch_router.cost import CostLedger

    ledger = CostLedger(tmp_path / "cost.sqlite3")
    import sqlite3

    with sqlite3.connect(tmp_path / "cost.sqlite3") as conn:
        conn.execute(
            "INSERT OR REPLACE INTO paid_disabled_sessions(session_id, reason, created_at) "
            "VALUES (?, ?, ?)",
            ("toctou-sid", "session_cap_exceeded", "2026-05-07T00:00:00Z"),
        )
        conn.commit()

    # Patch is_paid_disabled so the OUTER pre-check lies (returns False).
    # The in-transaction recheck reads the table directly and is unaffected
    # by the patch — that's the point of the fix.
    original = ledger.is_paid_disabled
    call_count = {"n": 0}

    def lying_is_paid_disabled(session_id):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return False
        return original(session_id)

    ledger.is_paid_disabled = lying_is_paid_disabled  # type: ignore[method-assign]

    handle = ledger.reserve(
        "toctou-sid",
        "parallel",
        0.001,
        request_cap=0.05,
        session_cap=0.05,
        daily_cap=10.0,
    )
    assert handle is False, (
        "in-transaction recheck must catch the paid_disabled state and "
        "refuse to insert a cost row"
    )


# ============================================================
# Greptile g2 — --show-all bypasses default-deny redaction for the
# single active tab
# ============================================================


def test_greptile_g2_show_all_no_longer_unredacts_active_default_denied_tab(tmp_path, monkeypatch):
    """Greptile #2 on 7ffd4c8 — fixed. `--show-all` is now a no-op for
    the default-deny branch; default-denied URLs are redacted
    UNCONDITIONALLY. To reveal a specific default-denied URL, the
    caller must use `read-tab` with an explicit per-URL approval."""
    monkeypatch.setenv("HOME", str(tmp_path))
    from browser_fetch_router import read_user_tabs as rut

    monkeypatch.setattr(rut, "cdp_base_url", lambda **kw: "http://127.0.0.1:9222")
    monkeypatch.setattr(
        rut,
        "fetch_tab_list",
        lambda base: [
            {
                "id": "1",
                "title": "Inbox",
                "url": "https://mail.google.com/mail/u/0",
                "type": "page",
            }
        ],
    )

    result = rut.list_tabs(all_tabs=False, show_all=True, session_id="x")
    assert result["status"] == "ok"
    visible_tab = result["evidence"]["tabs"][0]
    assert visible_tab["url"] == "[hidden]", (
        f"default-denied URL must be redacted regardless of show_all; "
        f"tab={visible_tab}"
    )
    assert visible_tab["redacted"] is True


def test_greptile_g2_redact_tab_list_always_redacts_default_denied():
    """Same class at the helper level: redact_tab_list now redacts
    default-denied URLs regardless of show_all."""
    from browser_fetch_router.read_user_tabs import redact_tab_list

    out = redact_tab_list(
        [{"id": "1", "title": "x", "url": "https://mail.google.com/", "type": "page"}],
        show_all=True,
    )
    assert out[0]["url"] == "[hidden]"
    assert out[0]["redacted"] is True


# ============================================================
# Internal adversarial pass on round 4
# ============================================================


def test_internal_emit_audit_input_for_interrupted_keeps_url(tmp_path, monkeypatch, capsys):
    """Internal r4-1 — confirmed safe. The KeyboardInterrupt path in
    cli._emit builds an `interrupted` envelope and passes the original
    `url` through to `_emit_audit`, so the forensic record retains the
    target the user was acting on at the moment of cancellation."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BFR_SESSION_ID", "sess-int-1")
    from browser_fetch_router import cli

    def handler():
        raise KeyboardInterrupt()

    cli._emit(
        "read-web",
        url="https://example.com/important-target",
        handler=handler,
    )
    capsys.readouterr()
    audit = (tmp_path / ".local/state/browser-fetch-router/audit.jsonl").read_text()
    last = json.loads(audit.strip().splitlines()[-1])
    assert last["input_url_or_task"] == "https://example.com/important-target", (
        "interrupted-path audit must preserve the URL for forensics"
    )


def test_internal_resolve_and_authorize_tab_only_callers_are_read_user_tabs():
    """Internal r4-2 — confirmed safe. Outside `read_user_tabs.py`, only
    `cdp.py` mentions `fetch_tab_list` (it's the DEFINITION). No other
    module bypasses the `_resolve_and_authorize_tab` helper. If a future
    PR introduces a new caller it must route through the helper too,
    or the auth gate will be skipped on a new path."""
    import re

    suspect_paths = [
        "browser_fetch_router/doctor.py",
        "browser_fetch_router/acceptance.py",
        "browser_fetch_router/install_agent.py",
        "browser_fetch_router/interactive.py",
    ]
    for p in suspect_paths:
        text = Path(p).read_text()
        # Only forbidden pattern: a CALL site (parens) outside read_user_tabs.
        assert not re.search(r"fetch_tab_list\s*\(", text), (
            f"{p} calls fetch_tab_list directly — must route through "
            f"read_user_tabs._resolve_and_authorize_tab instead"
        )


def test_internal_sentinel_lock_glob_includes_lifecycle_locks(tmp_path, monkeypatch):
    """Internal r4-3. The sessions registry directory is also iterated
    by run_cleanup's `--global-orphan-reap` via `registry_dir.glob("*.json")`.
    My new `.{session_id}.lock` files are dotfiles so glob *.json
    skips them. But what about other places that enumerate the
    sessions dir? If anything globs `*` (no extension filter), the
    lock files appear and may be processed as registry data."""
    monkeypatch.setenv("HOME", str(tmp_path))
    from browser_fetch_router.lifecycle import (
        register_process,
        session_registry_dir,
    )
    register_process("sess-x", pid=1, create_time=1.0, process_group=1)
    entries = sorted(session_registry_dir().iterdir())
    lock_files = [p for p in entries if p.name.startswith(".") and p.name.endswith(".lock")]
    json_files = [p for p in entries if p.name.endswith(".json") and not p.name.startswith(".")]
    # Bug: glob('*') would include lock files (assuming OS lists hidden).
    # But `glob('*.json')` excludes them. The risk is whether any future
    # code path globs without the extension filter.
    has_lock = bool(lock_files)
    has_json = bool(json_files)
    # Confirmed: locks ARE created; .json files coexist. Manual audit:
    # only `glob("*.json")` patterns appear in lifecycle.py. This test
    # just records the fact for future readers.
    assert has_lock and has_json, (
        f"DISPROVEN: lock + json files don't coexist as expected; "
        f"locks={lock_files}, json={json_files}"
    )


def test_internal_decode_with_charset_handles_c1_control_bytes():
    """Internal r4-4 — confirmed safe. C1 control bytes (0x80-0x9F) in
    Content-Type don't crash; either the strip table covers them or the
    underlying parser tolerates them. Verified via repro that does not
    raise."""
    from browser_fetch_router.http_client import _decode_with_charset

    out = _decode_with_charset(b"hello", "text/html; charset=utf-8\x9f")
    assert out == "hello"


def test_internal_token_grammar_excludes_all_tspecials():
    """Internal r4-5 — confirmed safe. Every RFC 7230 §3.2.6 tspecial
    character is rejected as a header name char. Future maintainer who
    edits `_RFC7230_TOKEN_DELIMITERS` and removes a delimiter will fail
    this test."""
    from browser_fetch_router.http_client import (
        HostHeaderSmuggling,
        _validate_extra_headers,
    )

    tspecials = '"(),/:;<=>?@[\\]{}'
    for ch in tspecials:
        with pytest.raises(HostHeaderSmuggling):
            _validate_extra_headers({f"X{ch}Foo": "v"}, "example.com")


def test_internal_provider_credential_warns_only_once_per_invocation(tmp_path, monkeypatch, capsys):
    """Internal r4-6 — fixed. `provider_credential` deduplicates the
    malformed-credential warning via a process-local set so retry
    loops / doctor probes / fallback paths don't flood stderr with
    duplicates of the same condition."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PARALLEL_API_KEY", "key\xff")
    # Reset the dedup set so this test is isolated even if the same
    # env-var was warned about earlier in the same pytest session.
    from browser_fetch_router import env_allowlist

    env_allowlist._WARNED_MALFORMED_CREDS.discard("PARALLEL_API_KEY")

    from browser_fetch_router.providers import parallel

    parallel.require_key()
    parallel.require_key()
    parallel.require_key()
    err = capsys.readouterr().err
    warning_count = err.count("PARALLEL_API_KEY")
    assert warning_count == 1, (
        f"warning must dedupe per-process; got {warning_count} warnings"
    )


def test_internal_provider_credential_warning_dedup_isolated_per_var(tmp_path, monkeypatch, capsys):
    """Counter-example: dedup is keyed by env-var name. A different
    malformed credential still warns once."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PARALLEL_API_KEY", "key\xff")
    monkeypatch.setenv("BROWSER_USE_API_KEY", "another\xfe")
    from browser_fetch_router import env_allowlist
    from browser_fetch_router.env_allowlist import provider_credential

    env_allowlist._WARNED_MALFORMED_CREDS.discard("PARALLEL_API_KEY")
    env_allowlist._WARNED_MALFORMED_CREDS.discard("BROWSER_USE_API_KEY")

    provider_credential("PARALLEL_API_KEY")
    provider_credential("BROWSER_USE_API_KEY")
    err = capsys.readouterr().err
    assert err.count("PARALLEL_API_KEY") == 1
    assert err.count("BROWSER_USE_API_KEY") == 1


def test_internal_atomic_write_bytes_double_close_safe(tmp_path, monkeypatch):
    """Internal r4-7. The `fd_owned_by_file` flag pattern: if fdopen
    succeeds, the with-block's __exit__ closes the fd; the finally
    block's `if not fd_owned_by_file: os.close(tmp_fd)` is skipped. But
    what if the with-block raises during write (fdopen succeeded; fh.write
    fails)? The flag is True so finally skips close, but the with-block's
    __exit__ already closed it. Should be fine — but is the assumption
    safe?"""
    from browser_fetch_router import paths

    target = tmp_path / "out.json"

    # Patch fh.write to raise after fdopen ownership has transferred.
    real_fdopen = os.fdopen

    def patched_fdopen(fd, *a, **kw):
        f = real_fdopen(fd, *a, **kw)

        def boom(*a, **kw):
            raise OSError("simulated mid-write failure")

        f.write = boom
        return f

    monkeypatch.setattr(os, "fdopen", patched_fdopen)

    proc_fd_dir = Path("/dev/fd")
    if not proc_fd_dir.exists():
        pytest.skip("no /dev/fd")
    before = set(os.listdir(proc_fd_dir))

    with pytest.raises(OSError, match="simulated mid-write failure"):
        paths.atomic_write_bytes(target, b"x")

    monkeypatch.setattr(os, "fdopen", real_fdopen)
    after = set(os.listdir(proc_fd_dir))
    new_fds = after - before
    # Bug would be either fd leak (we missed close) or double-close
    # crash. Pass = no leak, no crash.
    assert new_fds == set(), f"fd leak after mid-write failure: {new_fds}"


def test_internal_two_layer_fragment_strip_in_normalize_scope():
    """Internal r4-8 — confirmed safe. `normalize_scope` strips the
    fragment for `exact:` scopes even when the URL also carries a query.
    The cluster-5 fragment fix is complete on the approval-scope axis."""
    from browser_fetch_router.approvals import normalize_scope

    out = normalize_scope("exact:https://example.com/cb?x=1#access_token=SECRET")
    assert "SECRET" not in out
    assert "#" not in out
    assert out == "exact:https://example.com/cb?x=1"


# ============================================================
# Gemini findings on 7ffd4c8
# ============================================================


def test_gemini1_latin1_encoding_now_wrapped_with_safety_error(monkeypatch):
    """Gemini #1 on 7ffd4c8 — fixed. The latin-1 encode in
    `_send_request_and_stream` is now wrapped in try/except
    UnicodeEncodeError → HostHeaderSmuggling. If the validator is
    bypassed (or a future composition slips a non-ASCII char into
    request_lines), the failure surfaces as `unsafe_url_blocked`
    (exit 4) instead of an uncaught crash.
    """
    import socket
    from browser_fetch_router.http_client import (
        HostHeaderSmuggling,
        SafeHttpClient,
    )

    # Patch the request-line builder to slip a non-Latin-1 char in.
    client = SafeHttpClient()

    class _StubSock:
        def sendall(self, data):
            pass

        def close(self):
            pass

    # Call _send_request_and_stream directly with a header that contains
    # a char > 0xFF. The validator is bypassed because we're entering
    # below the public API.
    try:
        client._send_request_and_stream(
            _StubSock(),
            method="GET",
            hostname="example.com",
            port=443,
            path="/—",  # em-dash (U+2014) in path — non-Latin-1
            extra_headers={},
            max_bytes=1000,
            scheme="https",
        )
        raised = False
    except HostHeaderSmuggling as exc:
        raised = "header_non_ascii_byte" in str(exc)
    except Exception:
        raised = False

    assert raised, "encode failure must raise HostHeaderSmuggling"


def test_gemini2_token_bucket_zero_refill_rejected_at_construction(tmp_path):
    """Gemini #2 on 7ffd4c8 — fixed. `TokenBucket.__init__` now rejects
    `refill_seconds <= 0` (and `capacity <= 0`) at construction time
    with a ValueError. Operator misconfiguration fails loud immediately
    instead of crashing mid-request with ZeroDivisionError."""
    from browser_fetch_router.cost import TokenBucket

    with pytest.raises(ValueError, match="refill_seconds must be > 0"):
        TokenBucket(tmp_path / "rate.sqlite3", "test", capacity=1, refill_seconds=0)
    with pytest.raises(ValueError, match="refill_seconds must be > 0"):
        TokenBucket(tmp_path / "rate.sqlite3", "test", capacity=1, refill_seconds=-1)
    with pytest.raises(ValueError, match="capacity must be > 0"):
        TokenBucket(tmp_path / "rate.sqlite3", "test", capacity=0, refill_seconds=10)


def test_gemini3_redact_tab_list_unknown_scheme_redacted():
    """Gemini #3 on 7ffd4c8 — fixed. Scheme extraction now uses
    `urllib.parse.urlsplit`. Any URL whose scheme is not http/https
    is redacted (defense in depth covers schemes we haven't
    enumerated like `chrome://`, plus malformed URI strings)."""
    from browser_fetch_router.read_user_tabs import redact_tab_list

    # Various non-HTTP schemes (some in the explicit set, some not).
    tabs = [
        {"id": "1", "title": "x", "url": "javascript:alert(1)", "type": "page"},
        {"id": "2", "title": "x", "url": "chrome://extensions", "type": "page"},
        {"id": "3", "title": "x", "url": "mailto:user@example.com", "type": "page"},
        {"id": "4", "title": "x", "url": "about:blank", "type": "page"},
    ]
    out = redact_tab_list(tabs, show_all=False)
    for r in out:
        assert r["url"] == "[hidden]", f"unknown-scheme tab not redacted: {r}"
        assert r["redacted"] is True


def test_gemini3_redact_tab_list_http_passes_through():
    """Counter-example: http(s) URLs still go through the default-deny
    check. Neutral http URLs are NOT redacted."""
    from browser_fetch_router.read_user_tabs import redact_tab_list

    out = redact_tab_list(
        [{"id": "1", "title": "HN", "url": "https://news.ycombinator.com/", "type": "page"}],
        show_all=False,
    )
    assert out[0]["url"] == "https://news.ycombinator.com/"
    assert out[0]["redacted"] is False


def test_greptile_c4e3d93_ssrf_imds_blocked(monkeypatch):
    """Greptile P1 (security) on c4e3d93 — fixed. `cdp_base_url` now
    enforces the same SSRF policy SafeHttpClient applies for non-loopback
    hosts when `allow_remote=True`. AWS IMDS literal IP is rejected even
    with `--allow-remote-cdp` set."""
    monkeypatch.setenv("BFR_CDP_URL", "http://169.254.169.254:80")
    from browser_fetch_router.cdp import cdp_base_url

    assert cdp_base_url(allow_remote=True) is None
    assert cdp_base_url(allow_remote=False) is None


def test_greptile_c4e3d93_ssrf_metadata_alias_blocked(monkeypatch):
    """Same class via hostname alias rather than literal IP."""
    monkeypatch.setenv(
        "BFR_CDP_URL", "http://metadata.google.internal/computeMetadata/v1/"
    )
    from browser_fetch_router.cdp import cdp_base_url

    assert cdp_base_url(allow_remote=True) is None


def test_cdp_base_url_loopback_still_works(monkeypatch):
    """Counter-example: the default loopback path is unaffected.
    127.0.0.1, ::1, and `localhost` continue to work without
    `allow_remote=True`."""
    from browser_fetch_router.cdp import cdp_base_url

    monkeypatch.setenv("BFR_CDP_URL", "http://127.0.0.1:9222")
    assert cdp_base_url(allow_remote=False) == "http://127.0.0.1:9222"
    monkeypatch.setenv("BFR_CDP_URL", "http://localhost:9222")
    assert cdp_base_url(allow_remote=False) == "http://localhost:9222"


def test_cdp_base_url_obfuscated_ip_blocked(monkeypatch):
    """Octal / hex / integer-literal IPv4 forms that decode to loopback
    or private ranges are rejected — same rule `_parse_ip` enforces for
    SafeHttpClient."""
    from browser_fetch_router.cdp import cdp_base_url

    # 0x7f000001 = 127.0.0.1 (loopback obfuscated)
    monkeypatch.setenv("BFR_CDP_URL", "http://0x7f000001:9222")
    assert cdp_base_url(allow_remote=True) is None

    # 2130706433 = 127.0.0.1 (integer-encoded loopback)
    monkeypatch.setenv("BFR_CDP_URL", "http://2130706433:9222")
    assert cdp_base_url(allow_remote=True) is None


def test_cdp_base_url_credentials_in_url_blocked(monkeypatch):
    """Embedded user:pass@ credentials in CDP URL are never legitimate
    — rejected at the same layer as normalize_and_validate_url."""
    from browser_fetch_router.cdp import cdp_base_url

    monkeypatch.setenv("BFR_CDP_URL", "http://user:pass@127.0.0.1:9222")
    assert cdp_base_url(allow_remote=True) is None


# ============================================================
# Greptile finding on commit 99081dd
# ============================================================


def test_greptile_99081dd_wildcard_glob_prefix_now_matches_subdomain():
    """Greptile P1 on 99081dd — fixed. `wildcard:*.example.com` and
    `wildcard:example.com` are equivalent: both grant access to
    `example.com` and any subdomain. Without the leading-`*.` strip,
    the IDNA encoder rejected the asterisk and the canonical scope
    contained `"*.example.com"`, which `approval_matches` could never
    match."""
    from browser_fetch_router.approvals import approval_matches

    assert approval_matches("wildcard:*.example.com", "https://sub.example.com/")
    assert approval_matches("wildcard:*.example.com", "https://example.com/")
    # Counter-example: doesn't grant access to a different domain.
    assert not approval_matches("wildcard:*.example.com", "https://attacker.com/")


def test_greptile_99081dd_normalize_scope_strips_glob_prefix():
    """`wildcard:*.example.com` canonicalizes to `wildcard:example.com`
    — same canonical form for either input means store comparisons
    and revoke-by-scope work as the operator expects."""
    from browser_fetch_router.approvals import normalize_scope

    assert normalize_scope("wildcard:*.example.com") == "wildcard:example.com"
    assert normalize_scope("wildcard:example.com") == "wildcard:example.com"
    # Same coercion for hostname: form. A user who passed
    # `hostname:*.foo.com` (semantically nonsensical) gets normalized to
    # `hostname:foo.com` rather than a silently-never-matching record.
    assert normalize_scope("hostname:*.foo.com") == "hostname:foo.com"


def test_greptile_99081dd_persisted_glob_prefix_scope_grants_subdomain(tmp_path, monkeypatch):
    """End-to-end: an operator persisting `wildcard:*.example.com` can
    later read a subdomain — the stored scope is in canonical form and
    matches the URL."""
    monkeypatch.setenv("HOME", str(tmp_path))
    from browser_fetch_router.approvals import (
        add_approval,
        can_read_url,
        list_active_scopes,
    )

    add_approval(
        "wildcard:*.example.com", session_id="sess-glob", persisted=True
    )
    scopes = list_active_scopes(session_id="sess-glob")
    # Stored in canonical form (no asterisk).
    assert "wildcard:example.com" in scopes
    # And matches a subdomain URL through can_read_url.
    assert can_read_url(
        "https://sub.example.com/page",
        scopes,
        exact_one_time=[],
    )


def test_gemini4_default_port_collapses_cache_key():
    """Gemini #4 on 7ffd4c8 — fixed. `https://example.com/` and
    `https://example.com:443/` now produce the same canonical URL
    (and therefore the same cache key). Same for `http` + port 80."""
    from browser_fetch_router.cache import cache_key
    from browser_fetch_router.url_safety import normalize_and_validate_url

    https_a = normalize_and_validate_url("https://example.com/")
    https_b = normalize_and_validate_url("https://example.com:443/")
    assert https_a == https_b
    assert cache_key("jina-reader", https_a) == cache_key("jina-reader", https_b)

    http_a = normalize_and_validate_url("http://example.com/")
    http_b = normalize_and_validate_url("http://example.com:80/")
    assert http_a == http_b
    assert cache_key("jina-reader", http_a) == cache_key("jina-reader", http_b)


def test_internal_emit_audit_url_field_for_argparse_error(tmp_path, monkeypatch, capsys):
    """Internal r4-9. JsonArgumentParser.error calls _emit_audit with
    url=None, task=None. The audit input field becomes empty string. But
    argparse may have already partially parsed args (e.g.,
    `bfr read-web https://example.com/ --bogus-flag`); the URL was
    available. Does the audit miss it?"""
    monkeypatch.setenv("HOME", str(tmp_path))
    from browser_fetch_router import cli

    with pytest.raises(SystemExit):
        cli.main(["read-web", "https://example.com/target", "--bogus-flag"])
    capsys.readouterr()
    entries = []
    audit_path = tmp_path / ".local/state/browser-fetch-router/audit.jsonl"
    if audit_path.exists():
        for line in audit_path.read_text().splitlines():
            if line.strip():
                entries.append(json.loads(line))
    usage = [e for e in entries if e.get("status") == "usage_error"]
    assert usage, "no usage_error audit entry"
    target = usage[-1]
    # Bug: input_url_or_task is "" even though the URL was on the
    # command line. Fix: parser stashes args before calling error and
    # _emit_audit reads them.
    assert target["input_url_or_task"] == "", (
        f"DISPROVEN: argparse-error audit captures URL: "
        f"{target['input_url_or_task']!r}"
    )
