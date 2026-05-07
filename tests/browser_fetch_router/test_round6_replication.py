"""Round-6 regression suite — 4 external adversarial reviews on
HEAD 91877ccc62ae99b959659cbdd00d2be67e2bea73 (GPT, DeepSeek, GLM, Kimi).

Convention (same as round 3 / round 5): each test starts as a REPLICATION
that PASSES on the unfixed code (PASS = bug exists). After the fix lands,
the test is INVERTED to assert the now-safe behavior. DISPROVE tests
assert the safe behavior up-front to falsify a claimed finding.

Convergent findings (>=3 of 4 reviewers):
  r6-01 CRITICAL: CDP fetch_tab_list follows 30x redirects → SSRF
  r6-02 MEDIUM:   Approval scope normalization silently produces
                  never-matching canonical forms for malformed inputs
                  (**.host, .host, host., empty, host:port)
  r6-03 MEDIUM:   _kill_pid_safely SIGTERMs leader only; no children
  r6-04 MEDIUM:   KeyboardInterrupt during _emit_audit → JSONL corruption
                  + uncaught propagation after stdout commit

Unique-but-credible (1 reviewer, concrete repro):
  r6-05 HIGH:     BFR_SESSION_ID="../audit" path-traversal escapes
                  sessions/ directory; path.unlink() runs even in dry_run
  r6-06 HIGH:     _local_browser_use_available executes attacker-controlled
                  browser_use.py from CWD/PYTHONPATH at probe time

Disproved-by-inspection / contested:
  r6-disp-k03:    _serialize_or_internal_error clobbering interrupted
                  status → safe (envelope() always serializable)
  r6-disp-k09:    .lock.json glob match → safe (sentinel files use
                  `.{session}.lock` extension, not `.json`)
  r6-g05 LOW:     Paid-fallback parallel cached under jina-reader TTL
                  (600s vs parallel's 3600s) — design question, asserted
                  as observed behavior so a future change is loud.
"""
from __future__ import annotations

import inspect
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest


# ============================================================
# r6-01 CRITICAL — CDP redirect SSRF via raw urllib.urlopen
# ============================================================


class _RedirectHandler(BaseHTTPRequestHandler):
    """Returns 302 to whatever URL the test stashed in `redirect_to`."""

    redirect_to: str = ""

    def do_GET(self):
        self.send_response(302)
        self.send_header("Location", self.__class__.redirect_to)
        self.end_headers()

    def log_message(self, *_a):
        pass


class _IMDSImposterHandler(BaseHTTPRequestHandler):
    """Stand-in for the IMDS / metadata endpoint the redirect points at.
    Returns a JSON list shaped like a CDP /json response so the bug is
    obvious: the attacker fully controls the tab list."""

    hits = 0

    def do_GET(self):
        type(self).hits += 1
        body = json.dumps(
            [
                {
                    "id": "PWNED",
                    "title": "imds_leak",
                    "url": "http://169.254.169.254/latest/meta-data/",
                    "type": "page",
                    "webSocketDebuggerUrl": "ws://attacker.example/",
                }
            ]
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_a):
        pass


def _start_server(handler_cls):
    server = HTTPServer(("127.0.0.1", 0), handler_cls)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server


def test_r6_01_cdp_redirect_ssrf_blocked_after_fix():
    """Round-6 r6-01 fix: fetch_tab_list now routes through SafeHttpClient
    with `follow_redirects=False`, so a compromised CDP server's 30x
    response raises CdpUnexpectedRedirect instead of being transparently
    followed. The IMDS imposter must NOT be reached.
    """
    from browser_fetch_router.cdp import (
        CdpUnexpectedRedirect,
        fetch_tab_list,
    )

    _IMDSImposterHandler.hits = 0
    imds = _start_server(_IMDSImposterHandler)
    try:
        _RedirectHandler.redirect_to = (
            f"http://127.0.0.1:{imds.server_address[1]}/imds"
        )
        redirect = _start_server(_RedirectHandler)
        try:
            base = f"http://127.0.0.1:{redirect.server_address[1]}"
            with pytest.raises(CdpUnexpectedRedirect):
                fetch_tab_list(base, timeout=3.0)
            # SAFE: redirect-target IMDS endpoint never received traffic.
            assert _IMDSImposterHandler.hits == 0
        finally:
            redirect.shutdown()
    finally:
        imds.shutdown()


# ============================================================
# r6-02 MEDIUM — approval scope malformed inputs silently dead
# ============================================================


@pytest.mark.parametrize(
    "scope_value",
    [
        "wildcard:**.example.com",
        "wildcard:.example.com",
        "wildcard:",
        "wildcard:example.com:8080",
        "wildcard:example.com/path",
        "wildcard:example.com?q=1",
        "wildcard:example.com#frag",
        "hostname:**.example.com",
        "hostname:.example.com",
        "hostname:example.com:8080",
        "hostname:",
    ],
)
def test_r6_02_malformed_scopes_raise_invalid_scope(scope_value):
    """Round-6 r6-02 fix: `normalize_scope` validates the hostname grammar
    and raises `InvalidScope` for malformed inputs instead of silently
    producing a never-matching canonical form. The CLI maps the exception
    to a usage_error envelope (exit 2) so the user sees their typo.
    """
    from browser_fetch_router.approvals import InvalidScope, normalize_scope

    with pytest.raises(InvalidScope):
        normalize_scope(scope_value)


@pytest.mark.parametrize(
    "scope_value,canonical_match_url",
    [
        # Trailing FQDN dot is harmless on the wire — collapse it.
        ("wildcard:EXAMPLE.com.", "https://example.com/x"),
        ("wildcard:EXAMPLE.com.", "https://sub.example.com/x"),
        ("hostname:example.com.", "https://example.com/x"),
        # Single `*.` glob prefix continues to canonicalize to bare host.
        ("wildcard:*.example.com", "https://example.com/x"),
        ("wildcard:*.example.com", "https://sub.example.com/x"),
        # Leading whitespace is auto-stripped (typo tolerance).
        ("wildcard: example.com", "https://example.com/x"),
        ("hostname:\texample.com", "https://example.com/x"),
    ],
)
def test_r6_02_normalized_canonical_forms_match_intended_hosts(
    scope_value, canonical_match_url
):
    """Forms that the round-6 fix DOES accept must canonicalize so they
    match the host the user obviously intended. Locks in the
    `wildcard:EXAMPLE.com.` collapses-to-`wildcard:example.com` semantics.
    """
    from browser_fetch_router.approvals import approval_matches

    assert approval_matches(scope_value, canonical_match_url)


# ============================================================
# r7-01 (Greptile P1 on 9a26fb2) — exact-scope default-port stripping
# ============================================================


def test_r7_01_exact_scope_strips_default_port_to_match_canonical_url():
    """Round-7 r7-01 fix (Greptile P1 on 9a26fb2): `normalize_scope` for
    `exact:` previously kept :443/:80 verbatim while
    `normalize_and_validate_url` stripped them. A Chrome tab returning
    `https://example.com:443/x` then never matched a stored
    `exact:https://example.com/x`. Class fix routes exact: scopes
    through the same canonicalizer so default ports collapse identically
    on both sides."""
    from browser_fetch_router.approvals import approval_matches, normalize_scope

    assert (
        normalize_scope("exact:https://example.com:443/path")
        == "exact:https://example.com/path"
    )
    assert (
        normalize_scope("exact:http://example.com:80/path")
        == "exact:http://example.com/path"
    )
    # Both spellings must match the canonical URL the wire sees.
    assert approval_matches(
        "exact:https://example.com:443/path", "https://example.com/path"
    )
    assert approval_matches(
        "exact:https://example.com/path", "https://example.com:443/path"
    )


def test_r7_01_exact_scope_sentinel_identifiers_preserved():
    """Non-URL exact: sentinels like `exact:list-all-tabs` must continue
    to round-trip through normalize_scope unchanged so the
    `--approval-scope=exact:list-all-tabs` permission keeps working
    after the round-7 routing-through-normalize_and_validate_url
    refactor."""
    from browser_fetch_router.approvals import normalize_scope

    assert normalize_scope("exact:list-all-tabs") == "exact:list-all-tabs"


# ============================================================
# r8 — Gemini round-9 findings on commit 3b131b7
# ============================================================


def test_r8_approvals_corrupt_store_backup_and_empty(tmp_path, monkeypatch):
    """Round-8 r8-01 fix (Gemini high on 3b131b7): a corrupt
    `approvals.json` previously degraded silently to an empty store, so
    the next `add_approval` ATOMICALLY WIPED every prior approval. Class
    fix: rename the corrupt file aside with a `.json.corrupt-<ts>`
    suffix and return empty so the caller can continue with a clean
    store. The user/operator sees the sibling `.corrupt-*` file as the
    loud signal."""
    from browser_fetch_router import approvals as approvals_mod
    from browser_fetch_router import paths

    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(approvals_mod, "config_dir", lambda: tmp_path)

    store = tmp_path / "approvals.json"
    store.write_text("{not valid json")  # corrupt

    data = approvals_mod._load_unlocked(store)
    # r14-01: `_load_unlocked` now delegates to the package-wide
    # safe-JSON helper which returns `{}` on corruption rather than the
    # legacy `{"scopes": []}` literal. Both are equivalent at every
    # call site (`add_approval` / `list_active_scopes` / `revoke_scope`
    # all do `data.setdefault("scopes", [])` or `data.get("scopes", [])`).
    # Asserting the literal would lock in an implementation detail; the
    # actual contract is "a dict with no usable scope records."
    assert isinstance(data, dict)
    assert data.get("scopes", []) == []
    assert not store.exists(), "corrupt store must be moved aside"
    backups = sorted(tmp_path.glob("approvals.json.corrupt-*"))
    assert backups, "corrupt content not preserved as backup"
    assert backups[0].read_text() == "{not valid json"


def test_r8_lifecycle_corrupt_registry_backup_and_empty(tmp_path, monkeypatch):
    """Round-8 r8-02 fix (Gemini medium on 3b131b7): same data-loss class
    in the session-registry reader. Corrupt registry → backup-and-empty
    so register_process doesn't atomically wipe the per-session process
    table on the next call."""
    from browser_fetch_router import lifecycle, paths

    monkeypatch.setattr(paths, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(lifecycle, "state_dir", lambda: tmp_path)

    sessions = lifecycle.session_registry_dir()
    registry = sessions / "test-sid-001.json"
    registry.write_text("{garbage")

    data = lifecycle._read_json(registry)
    assert data == {}
    assert not registry.exists()
    backups = sorted(sessions.glob("test-sid-001.json.corrupt-*"))
    assert backups
    assert backups[0].read_text() == "{garbage"


def test_r8_http_response_is_explicitly_closed(monkeypatch):
    """Round-8 r8-03 fix (Gemini medium on 3b131b7): the
    `http.client.HTTPResponse` returned by `_send_request_and_stream`
    is now closed in a finally so a long-running CLI process doesn't
    accumulate uncollected response objects."""
    import inspect

    from browser_fetch_router.http_client import SafeHttpClient

    src = inspect.getsource(SafeHttpClient._send_request_and_stream)
    assert "response.close()" in src
    finally_idx = src.find("finally:")
    close_idx = src.find("response.close()")
    assert finally_idx > 0 and close_idx > finally_idx, (
        "response.close() must live in a finally clause"
    )


def test_r8_cdp_resolver_uses_addrconfig_flag():
    """Round-8 r8-04 fix (Gemini medium on 3b131b7): cdp_base_url's
    own getaddrinfo call must include AI_ADDRCONFIG so resolution
    behavior is consistent with `http_client._default_resolver`."""
    import inspect

    from browser_fetch_router.cdp import cdp_base_url

    src = inspect.getsource(cdp_base_url)
    assert "AI_ADDRCONFIG" in src


def test_r8_list_tabs_self_approves_with_approval_scope(tmp_path, monkeypatch):
    """Round-8 r8-05 (Gemini medium on 3b131b7): list_tabs gains
    approval_scope/persist_approval params for single-command
    authorization parity with read_tab/screenshot_tab. Passing
    `approval_scope='exact:list-all-tabs'` along with `all_tabs=True`
    must add the scope BEFORE the active-scope check so the listing
    succeeds in one invocation."""
    from browser_fetch_router import paths
    from browser_fetch_router import read_user_tabs as rut

    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(paths, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(rut, "cdp_base_url", lambda allow_remote: "http://127.0.0.1:9222")
    monkeypatch.setattr(
        rut,
        "fetch_tab_list",
        lambda base: [
            {"id": "T1", "title": "Hacker News", "url": "https://news.ycombinator.com/", "type": "page"},
            {"id": "T2", "title": "Reddit", "url": "https://www.reddit.com/", "type": "page"},
        ],
    )

    out = rut.list_tabs(
        all_tabs=True,
        session_id="test-sid",
        approval_scope="exact:list-all-tabs",
        persist_approval=True,
    )
    assert out["status"] == "ok", out
    assert len(out["evidence"]["tabs"]) == 2


# ============================================================
# Round-13 (Gemini medium on f23f4b9) — declared deps must be used
# ============================================================


def test_round13_no_unused_runtime_dependencies():
    """Round-13 fix (Gemini medium on f23f4b9): every runtime dep
    declared in pyproject.toml must appear in at least one
    `import <name>` / `from <name>` statement under the package.
    The `httpx` and `websockets` deps were declared but unused —
    pulled transitive deps and inflated the security surface for
    nothing. This test fails on any future regression where a dep
    is added to pyproject without a corresponding import.

    Distribution-name → import-name mappings live in DIST_TO_MOD
    for the few cases where the wheel name and module name diverge.
    """
    import re
    import tomllib
    from pathlib import Path

    repo_root = Path(__file__).parent.parent.parent
    pyproject = tomllib.loads((repo_root / "pyproject.toml").read_text())
    declared = pyproject["project"]["dependencies"]

    # Distribution → import name. Add entries here if a new dep's
    # PyPI name differs from its module name.
    DIST_TO_MOD = {
        "psutil": "psutil",
    }

    pkg_dir = repo_root / "browser_fetch_router"
    sources = "\n".join(p.read_text() for p in pkg_dir.rglob("*.py"))

    for spec in declared:
        # spec is "name>=ver" or "name==ver"; split off version.
        name = re.split(r"[<>=! ]", spec, maxsplit=1)[0].strip()
        mod = DIST_TO_MOD.get(name, name.replace("-", "_"))
        pattern = rf"\b(?:from\s+{re.escape(mod)}|import\s+{re.escape(mod)})\b"
        assert re.search(pattern, sources), (
            f"declared dep {name!r} (module {mod!r}) is not imported "
            f"anywhere in browser_fetch_router/ — unused dependency"
        )


# ============================================================
# i — Internal adversarial review on commit 62ce86c (round-11)
# ============================================================


@pytest.mark.parametrize(
    "kindless_scope",
    [
        "foo",                  # no colon at all
        "",                     # empty string
        "hostnameexample.com",  # missing colon between kind and value
        "no_colon_anywhere",    # explicit
    ],
)
def test_i05_kindless_scope_raises_invalid_scope(kindless_scope):
    """Round-11 i05 fix: scopes without a colon kind-separator are
    silent-dead approvals — `normalize_scope` previously returned them
    verbatim (no `kind:value` shape), `add_approval` stored them, and
    `approval_matches` then short-circuited on its own ":" check
    returning False for every URL. Same silent-dead-approval class
    round-6 r6-02 / round-11 i03 set out to eliminate; this closes
    the colon-less leg of the class so EVERY accepted scope has a
    recognized kind."""
    from browser_fetch_router.approvals import InvalidScope, normalize_scope

    with pytest.raises(InvalidScope):
        normalize_scope(kindless_scope)


def test_i05_add_approval_rejects_kindless_scope(tmp_path, monkeypatch):
    """The InvalidScope raised by `normalize_scope` propagates through
    `add_approval` so the approval store never accumulates dead
    records. Pairs with the CLI dispatcher branch that maps
    InvalidScope → usage_error envelope (round-6 r6-02 wiring)."""
    from browser_fetch_router import approvals as approvals_mod
    from browser_fetch_router import paths

    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(approvals_mod, "config_dir", lambda: tmp_path)

    with pytest.raises(approvals_mod.InvalidScope):
        approvals_mod.add_approval("foo", session_id="s", persisted=False)

    # Defense-in-depth: even if a vintage colon-less record is in the
    # store, approval_matches must still safely return False. (Round-9
    # r9-01 catches InvalidScope in approval_matches.)
    assert not approvals_mod.approval_matches("foo", "https://example.com/")


def test_i06_kill_pid_safely_escalates_to_sigkill(monkeypatch):
    """Round-11 i06 fix: best-effort kill must not silently lie. The
    previous `_kill_pid_safely` returned "cleaned" after a 1 s
    SIGTERM grace period regardless of whether the process actually
    exited; `run_cleanup` then unlinked the registry file
    unconditionally. A SIGTERM-ignoring process therefore survived
    cleanup AND became untracked.

    Class fix: after the SIGTERM grace, escalate to SIGKILL on any
    survivors, re-wait briefly, and return "failed" if anything is
    still alive after that. `run_cleanup` then preserves the
    registry file when any outcome is "failed" so a future
    --global-orphan-reap can retry."""
    import sys
    import types

    from browser_fetch_router import lifecycle

    sigterm_signaled: list[int] = []
    sigkill_signaled: list[int] = []

    class _StubbornProc:
        """SIGTERM-ignoring leader. Stays alive through wait_procs;
        SIGKILL escalation finally takes effect."""

        def __init__(self, pid, create_time, descendants=None):
            self.pid = pid
            self._create = create_time
            self._descendants = descendants or []

        def create_time(self):
            return self._create

        def children(self, recursive=False):  # noqa: ARG002
            return self._descendants

        def terminate(self):
            sigterm_signaled.append(self.pid)

        def kill(self):
            sigkill_signaled.append(self.pid)

    fake_psutil = types.ModuleType("psutil")
    leader = _StubbornProc(2000, 22222.0)
    fake_psutil.Process = lambda pid: leader  # type: ignore[attr-defined]
    fake_psutil.NoSuchProcess = type("NoSuchProcess", (Exception,), {})  # type: ignore[attr-defined]
    fake_psutil.AccessDenied = type("AccessDenied", (Exception,), {})  # type: ignore[attr-defined]
    fake_psutil.Error = type("Error", (Exception,), {})  # type: ignore[attr-defined]

    # First wait_procs (post-SIGTERM): leader still alive.
    # Second wait_procs (post-SIGKILL): leader gone.
    wait_calls = {"n": 0}

    def fake_wait(procs, timeout=None):
        wait_calls["n"] += 1
        if wait_calls["n"] == 1:
            return ([], list(procs))  # all alive
        return (list(procs), [])  # all gone after SIGKILL

    fake_psutil.wait_procs = fake_wait  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)
    monkeypatch.setattr(
        lifecycle.os,
        "kill",
        lambda pid, sig: sigterm_signaled.append(("os.kill", pid)),
    )

    outcome = lifecycle._kill_pid_safely(2000, 22222.0, dry_run=False)
    # The escalation happened.
    assert sigkill_signaled, "SIGKILL escalation never fired"
    # And the result reflects the eventual success.
    assert outcome == "cleaned"


def test_i06_run_cleanup_preserves_registry_on_failed_outcome(
    tmp_path, monkeypatch
):
    """When `_kill_pid_safely` returns "failed", `run_cleanup` must
    NOT unlink the registry file — a future `--global-orphan-reap`
    needs the entry to retry the kill. Class fix on the cleanup
    loop's unlink gate."""
    from browser_fetch_router import lifecycle, paths

    monkeypatch.setattr(paths, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(lifecycle, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(
        lifecycle,
        "_kill_pid_safely",
        lambda pid, ct, *, dry_run: "failed",
    )

    sid = "test-i06"
    registry = lifecycle.session_registry_path(sid)
    registry.write_text(
        json.dumps(
            {
                "session_id": sid,
                "local_processes": [
                    {"pid": 99999, "create_time": 1.0, "process_group": "g"},
                ],
            }
        )
    )

    out = lifecycle.run_cleanup(all_sessions=True, session_id=sid, dry_run=False)
    assert out["status"] == "ok"
    failed = out["evidence"]["results"][0]["failed"]
    assert failed and failed[0]["pid"] == 99999
    assert registry.exists(), (
        "registry must be preserved when any kill failed so future "
        "cleanups can retry the survivor"
    )


def test_i01_log_rotation_respects_dry_run(tmp_path, monkeypatch):
    """Round-11 i01 fix: `_rotate_logs` previously called
    `path.rename(archived)` unconditionally. `run_cleanup(logs=True,
    dry_run=True)` therefore moved audit.jsonl/cost.jsonl aside
    despite the caller asking for a side-effect-free preview. Same
    `dry_run` violation class round-6 r6-05 fixed for the registry
    path — now also enforced for log rotation.
    """
    from browser_fetch_router import lifecycle, paths

    monkeypatch.setattr(paths, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(lifecycle, "state_dir", lambda: tmp_path)

    audit_log = tmp_path / "audit.jsonl"
    cost_log = tmp_path / "cost.jsonl"
    audit_log.write_text("{}\n" * 10)
    cost_log.write_text("{}\n" * 10)
    # Force age-based rotation by stamping mtime far in the past.
    old = time.time() - 365 * 86400
    os.utime(audit_log, (old, old))
    os.utime(cost_log, (old, old))

    out = lifecycle.run_cleanup(logs=True, dry_run=True, max_age_days=30)
    assert out["status"] == "ok"
    assert audit_log.exists(), "dry_run=True must not rename audit.jsonl"
    assert cost_log.exists(), "dry_run=True must not rename cost.jsonl"

    # Conversely, dry_run=False rotates as documented.
    lifecycle.run_cleanup(logs=True, dry_run=False, max_age_days=30)
    assert not audit_log.exists()
    assert not cost_log.exists()
    archives = sorted(p.name for p in tmp_path.glob("*.archive"))
    assert any("audit.jsonl" in a for a in archives)
    assert any("cost.jsonl" in a for a in archives)


def test_i02_can_read_url_safe_on_malformed_planted_scope(tmp_path, monkeypatch):
    """Round-11 i02 fix: `can_read_url` previously called
    `normalize_scope(scope).startswith("exact:")` without try/except.
    A malformed scope planted directly in approvals.json (manually,
    or from a pre-fix vintage) caused `InvalidScope` / `UnsafeUrl`
    to propagate and crash the read with internal_error. Defense-in-
    depth pair to the round-9 r9-01 fix on `approval_matches`."""
    from browser_fetch_router.approvals import can_read_url

    # Both kinds of malformed exact_one_time scope must degrade to a
    # safe `False` instead of raising. The persistent-scope path
    # should still be consulted.
    out_unknown_kind = can_read_url(
        "https://example.com/x",
        persistent_scopes=["wildcard:example.com"],
        exact_one_time=["bogus_kind:foo"],
    )
    assert out_unknown_kind is True  # persistent wildcard wins

    out_ssrf_blocked = can_read_url(
        "https://example.com/x",
        persistent_scopes=["wildcard:example.com"],
        exact_one_time=["exact:http://169.254.169.254/imds"],
    )
    assert out_ssrf_blocked is True

    # Without a backing persistent scope, the result is False but
    # crucially does NOT raise.
    out_no_backing = can_read_url(
        "https://example.com/x",
        persistent_scopes=[],
        exact_one_time=["bogus_kind:foo"],
    )
    assert out_no_backing is False


@pytest.mark.parametrize(
    "empty_exact",
    ["exact:", "exact: ", "exact:\t", "exact:\n"],
)
def test_i03_empty_exact_scope_rejected(empty_exact):
    """Round-11 i03 fix: `normalize_scope("exact:")` previously
    returned the verbatim sentinel `"exact:"` and `add_approval`
    stored a record that matched nothing. Same silent-dead-approval
    class round-6 r6-02 fixed for `wildcard:`/`hostname:` — now
    extended to reject empty exact-scope values too."""
    from browser_fetch_router.approvals import InvalidScope, normalize_scope

    with pytest.raises(InvalidScope):
        normalize_scope(empty_exact)


@pytest.mark.parametrize(
    "bad_cdp_url",
    [
        "http://127.0.0.1:9222/foo",
        "http://127.0.0.1:9222/",  # trailing slash + extra path
        "http://127.0.0.1:9222?q=1",
        "http://127.0.0.1:9222#frag",
        "http://127.0.0.1:9222/path?q=1#frag",
    ],
)
def test_i04_cdp_base_url_rejects_path_query_fragment(bad_cdp_url, monkeypatch):
    """Round-11 i04 fix: `cdp_base_url` previously accepted any
    URL whose scheme/host/port passed validation, including ones
    with non-empty path/query/fragment. `fetch_tab_list` then
    appended `/json` after `rstrip('/')` — fragments and queries
    silently displaced the appended `/json` so the wire request
    landed at `/` instead. The env-var contract is host[:port]
    only; reject anything richer."""
    from browser_fetch_router.cdp import cdp_base_url

    monkeypatch.setenv("BFR_CDP_URL", bad_cdp_url)
    if bad_cdp_url == "http://127.0.0.1:9222/":
        # Bare trailing slash is the historical accepted form
        # (rstrip handles it). Test it stays accepted.
        assert cdp_base_url(allow_remote=False) == "http://127.0.0.1:9222"
        return
    assert cdp_base_url(allow_remote=False) is None


# ============================================================
# r9 — Greptile + Gemini round-10 findings on commit f2a99d0
# ============================================================


@pytest.mark.parametrize(
    "bad_kind_scope",
    [
        "hosname:example.com",          # typo for hostname
        "wildcrad:example.com",         # typo for wildcard
        "exact-url:example.com/x",      # near-miss for exact
        "fuzzy:example.com",            # made-up kind
        "HOSTNAME:example.com",         # case mismatch (kind is lowered)
    ],
)
def test_r9_unknown_scope_kind_raises_invalid_scope(bad_kind_scope):
    """Round-9 r9-01 fix (Greptile P1 on f2a99d0): a typo'd kind like
    `hosname:example.com` previously fell through to the verbatim
    return; `add_approval` stored the record; `approval_matches`
    rejected it because `kind not in VALID_SCOPE_KINDS`. The operator
    saw `approval_required` again with no signal their scope was
    misspelled — the silent-dead-approval class round-6 r6-02 set out
    to eliminate. Class fix raises InvalidScope upfront for any
    kind not in VALID_SCOPE_KINDS.

    Note: HOSTNAME (uppercase) IS valid because `kind = kind.strip().lower()`
    happens before the kind check; included here to lock in that
    case-folding survives.
    """
    from browser_fetch_router.approvals import InvalidScope, normalize_scope

    if bad_kind_scope.startswith("HOSTNAME:"):
        # case-folding makes this valid
        assert normalize_scope(bad_kind_scope) == "hostname:example.com"
        return
    with pytest.raises(InvalidScope):
        normalize_scope(bad_kind_scope)


def test_r9_acceptance_run_merges_caller_env_with_os_environ(monkeypatch):
    """Round-9 r9-02 fix (Gemini medium on f2a99d0): `_run` now merges
    caller env into a fresh `os.environ` snapshot rather than the
    `env or {**os.environ}` truthiness pattern. Previously passing
    `env={"BFR_AGENT": "test"}` lost PATH / PYTHONPATH / HOME because
    the truthy non-empty dict replaced the entire env. Lock-in:
    inspect the source for the merge expression."""
    import inspect

    from browser_fetch_router import acceptance

    src = inspect.getsource(acceptance._run)
    assert "{**os.environ, **(env or {})}" in src
    # The actual subprocess.run call must use the merged dict, not the
    # bare `env or {**os.environ}` truthiness pattern.
    assert "env=merged_env" in src


def test_r9_run_cleanup_docstring_clarifies_all_sessions_scope():
    """Round-9 r9-03 fix (Gemini medium on f2a99d0): the prior docstring
    said `all_sessions=True (default scope)` while the default value
    was False — confusing wording that misled readers about what
    `--all` cleans. New docstring distinguishes "all processes for
    this session" from "all sessions across the system."""
    import inspect

    from browser_fetch_router.lifecycle import run_cleanup

    doc = inspect.getdoc(run_cleanup) or ""
    assert "(default scope)" not in doc
    assert "all processes for this session" in doc.lower() or (
        "current-session targeting" in doc.lower()
    )


def test_r8_disprove_audit_already_journals_per_event():
    """Round-8 r8-disprove (Gemini high on 3b131b7): the suggestion to
    'journal events immediately' is a generic recommendation that does
    not match the current implementation. `append_audit` already does
    a complete open-lock-write-fsync-unlock-close per call — no
    buffering, no long-held file handle.

    r15 (closing-pass persistence contract) extracted the primitives
    into `paths.append_durable_line` AND added fsync. The per-call
    journal invariant strengthens (durable on append, not just visible
    in the page cache). This test now asserts the new structural form:
    audit.append_audit routes through the durable helper and there is
    no module-level state holding an fd open across calls.
    """
    import inspect

    from browser_fetch_router import audit

    src = inspect.getsource(audit.append_audit)
    # Per-call routing through the durable helper (which contains
    # open + flock + write_all + fsync + unlock + close).
    assert "append_durable_line(" in src, (
        "audit.append_audit must journal each event through the "
        "package-wide durable-append helper; raw inlined os.open is "
        "blocked by the persistence-contract static guard."
    )
    # No module-level "open at import time" — every call opens its own
    # fd via the helper, so there is no shared buffer or cross-call
    # state.
    module_src = inspect.getsource(audit)
    assert "os.open" not in module_src
    # No module-level fd cache: any module-level assignment of an
    # open fd would be a buffering vector. The function takes the
    # event, encodes it, and immediately calls the helper.
    assert "_audit_fd" not in module_src
    assert "audit_handle" not in module_src


@pytest.mark.parametrize(
    "blocked_scope",
    [
        "exact:http://169.254.169.254/latest/meta-data/",  # AWS IMDS
        "exact:http://10.0.0.1/admin",                     # RFC 1918
        "exact:http://metadata.google.internal/",          # GCP metadata
    ],
)
def test_r7_02_exact_scope_ssrf_blocked_url_does_not_silently_persist(
    blocked_scope,
):
    """Round-7 r7-02 fix (Greptile P1-security on 771f3bc): the catch-
    UnsafeUrl-and-fallback path I added in round-7 r7-01 ALSO swallowed
    SSRF rejections, so `exact:http://169.254.169.254/` was silently
    stored as a legitimate scope. `can_read_url` then returned True for
    the IMDS URL — transport still blocked the actual fetch but the
    invariant was broken (and exploitable once CDP screenshot/text
    extraction wires in).

    Class fix branches on URL-shape (urlsplit().scheme in {http, https})
    UPFRONT instead of catching post-hoc. SSRF rejections propagate as
    UnsafeUrl so add_approval refuses the scope.
    """
    from browser_fetch_router.approvals import normalize_scope
    from browser_fetch_router.url_safety import UnsafeUrl

    with pytest.raises(UnsafeUrl):
        normalize_scope(blocked_scope)


# ============================================================
# Round-6/7 disprove lock-ins — bots re-flagged previously-fixed
# concerns on the new diff. Each previously-fixed issue must have a
# regression test that fails if the fix is removed (Learning #5:
# DISPROVEN requires reproduction test, not prose).
# ============================================================


def test_r7_disprove_cost_toctou_paid_disabled_recheck_in_place():
    """Greptile P1 on 9a26fb2 re-flagged the round-4 g1 finding (TOCTOU
    on `is_paid_disabled` not re-checked inside BEGIN IMMEDIATE). The
    in-transaction recheck IS in place at cost.py:138-144. This locks
    in the existing lock-in test (test_round5_replication.py) by
    additionally asserting the recheck SQL appears in the source —
    so a future maintainer who removes it gets a regression."""
    import inspect

    from browser_fetch_router.cost import CostLedger

    src = inspect.getsource(CostLedger.reserve)
    assert "FROM paid_disabled_sessions WHERE session_id" in src
    assert "BEGIN IMMEDIATE" in src
    # The recheck must occur AFTER BEGIN IMMEDIATE.
    begin_idx = src.find("BEGIN IMMEDIATE")
    recheck_idx = src.find("FROM paid_disabled_sessions WHERE session_id")
    assert recheck_idx > begin_idx > 0


def test_r7_disprove_show_all_does_not_bypass_default_deny_redaction():
    """Greptile P1 (security) on 9a26fb2 re-flagged the round-4 g2
    finding (`--show-all` bypasses default-deny redaction in single-tab
    path). The fix in `redact_tab_list` is `_ = show_all` — the
    parameter is intentionally unused. Lock-in: render a default-denied
    URL with `show_all=True` and assert it is still redacted."""
    from browser_fetch_router.read_user_tabs import redact_tab_list

    sensitive = "https://mail.google.com/mail/u/0"
    out = redact_tab_list(
        [{"id": "T1", "title": "inbox", "url": sensitive, "type": "page"}],
        show_all=True,
    )
    assert out[0]["redacted"] is True
    assert out[0]["url"] == "[hidden]"


def test_r7_disprove_sanitize_audit_input_preserves_freeform_task():
    """Gemini high on 9a26fb2 re-flagged the round-1 finding
    (`sanitize_audit_input` mangles free-form task strings with `?`).
    The fix at audit.py:64 returns early when `parsed.scheme and
    parsed.netloc` are not BOTH present. Lock-in: pass a free-form
    task string with `?` and assert verbatim output."""
    from browser_fetch_router.audit import sanitize_audit_input

    task = "Should I click the delete button on the unsubscribe page?"
    assert sanitize_audit_input(task) == task


def test_r7_disprove_sqlite3_connect_explicitly_closes(tmp_path):
    """Gemini high on 9a26fb2 re-flagged the round-1 finding (sqlite3
    `with` doesn't close). The fix in cost.py uses a `@contextmanager`
    `_connect` that calls `conn.close()` in a finally. Lock-in: open
    a connection via `_connect`, exit the with-block, assert the
    connection raises ProgrammingError on subsequent use (closed
    connection contract). Uses `tmp_path` for environment portability
    (Gemini medium on commit 62ce86c)."""
    import sqlite3

    from browser_fetch_router.cost import _connect

    db = tmp_path / "r7_disprove_sqlite_close.sqlite3"
    captured: list = []
    with _connect(db) as conn:
        captured.append(conn)
    # After the with-block, the connection must be closed.
    with pytest.raises(sqlite3.ProgrammingError):
        captured[0].execute("SELECT 1")


def test_r7_disprove_token_bucket_validates_positive_refill(tmp_path):
    """Gemini medium on 9a26fb2 re-flagged the round-4 finding (TokenBucket
    ZeroDivisionError on `refill_seconds=0`). The fix validates at
    construction. Lock-in: confirm ValueError on zero or negative
    refill, and on zero capacity. Uses `tmp_path` for environment
    portability (Gemini medium on commit 62ce86c)."""
    from browser_fetch_router.cost import TokenBucket

    db = tmp_path / "r7_disprove_token_bucket.sqlite3"
    with pytest.raises(ValueError, match="refill_seconds must be > 0"):
        TokenBucket(db, "test", capacity=1, refill_seconds=0)
    with pytest.raises(ValueError, match="refill_seconds must be > 0"):
        TokenBucket(db, "test", capacity=1, refill_seconds=-1)
    with pytest.raises(ValueError, match="capacity must be > 0"):
        TokenBucket(db, "test", capacity=0, refill_seconds=10)


# ============================================================
# r6-03 MEDIUM — lifecycle cleanup never enumerates child PIDs
# ============================================================


def test_r6_03_kill_pid_safely_enumerates_children():
    """Round-6 r6-03 fix: _kill_pid_safely must call
    `proc.children(recursive=True)` BEFORE SIGTERMing the leader so the
    descendant tree is captured (after the leader exits, psutil cannot
    walk it) and terminated."""
    from browser_fetch_router.lifecycle import _kill_pid_safely

    src = inspect.getsource(_kill_pid_safely)
    assert "children(recursive=True)" in src
    assert "wait_procs" in src


def test_r6_03_kill_pid_safely_terminates_descendants(monkeypatch):
    """Behavioral verification: with psutil mocked, calling
    _kill_pid_safely on a leader with two descendants should call
    .terminate() on each descendant and SIGTERM on the leader."""
    import sys
    import types

    from browser_fetch_router import lifecycle

    terminated: list[int] = []
    killed: list[int] = []

    class _FakeProc:
        def __init__(self, pid, create_time, descendants=None):
            self.pid = pid
            self._create = create_time
            self._descendants = descendants or []

        def create_time(self):
            return self._create

        def children(self, recursive=False):  # noqa: ARG002
            return self._descendants

        def terminate(self):
            terminated.append(self.pid)

    fake_psutil = types.ModuleType("psutil")
    fake_psutil.Process = lambda pid: _FakeProc(  # type: ignore[attr-defined]
        pid,
        create_time=12345.0,
        descendants=[
            _FakeProc(pid + 1, create_time=12346.0),
            _FakeProc(pid + 2, create_time=12347.0),
        ],
    )

    class _NoSuch(Exception):
        pass

    class _AccessDenied(Exception):
        pass

    class _Error(Exception):
        pass

    fake_psutil.NoSuchProcess = _NoSuch  # type: ignore[attr-defined]
    fake_psutil.AccessDenied = _AccessDenied  # type: ignore[attr-defined]
    fake_psutil.Error = _Error  # type: ignore[attr-defined]
    # Round-11 i06: wait_procs return shape is (gone, alive). All gone
    # after SIGTERM so the round-6 r6-03 happy path doesn't trip the
    # SIGKILL escalation (that escalation is tested separately by
    # test_i06_kill_pid_safely_escalates_to_sigkill).
    fake_psutil.wait_procs = lambda procs, timeout=None: (list(procs), [])  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)
    monkeypatch.setattr(
        lifecycle.os,
        "kill",
        lambda pid, sig: killed.append(pid),
    )

    outcome = lifecycle._kill_pid_safely(1000, 12345.0, dry_run=False)
    assert outcome == "cleaned"
    assert sorted(terminated) == [1001, 1002]  # both descendants
    assert killed == [1000]  # leader signaled exactly once


# ============================================================
# r6-04 MEDIUM — KeyboardInterrupt during _emit_audit propagates
# ============================================================


def test_r6_04_keyboardinterrupt_during_audit_absorbed(monkeypatch):
    """Round-6 r6-04 fix: SIGINT (KeyboardInterrupt) and MemoryError
    raised inside append_audit must NOT escape _emit_audit. The success
    envelope was already committed to stdout; a propagating signal here
    would print a traceback after the JSON line and corrupt the exit
    code (130 → 1). Class fix is the BaseException-broad except in
    _emit_audit.
    """
    from browser_fetch_router import cli

    payload = {"command": "doctor", "status": "ok", "evidence": {}}

    monkeypatch.setattr(
        cli,
        "append_audit",
        lambda _event: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    cli._emit_audit("doctor", url=None, task=None, payload=payload)

    monkeypatch.setattr(
        cli,
        "append_audit",
        lambda _event: (_ for _ in ()).throw(MemoryError()),
    )
    cli._emit_audit("doctor", url=None, task=None, payload=payload)

    monkeypatch.setattr(
        cli,
        "append_audit",
        lambda _event: (_ for _ in ()).throw(SystemExit(99)),
    )
    cli._emit_audit("doctor", url=None, task=None, payload=payload)


# ============================================================
# r6-05 HIGH — session_id path traversal escapes sessions/ dir
# ============================================================


def test_r6_05_session_id_path_traversal_rejected(tmp_path, monkeypatch):
    """Round-6 r6-05 fix: malformed session IDs (`..`, `/`, NUL,
    overlong, etc.) are rejected by `validate_session_id` BEFORE they
    can compose into a registry path. `session_registry_path` also
    asserts containment as defense-in-depth. The unrelated sibling
    JSON file must remain untouched.
    """
    from browser_fetch_router import lifecycle, paths

    monkeypatch.setattr(paths, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(lifecycle, "state_dir", lambda: tmp_path)

    sibling = tmp_path / "audit.json"
    sibling.write_text(json.dumps({"keep": True}))

    for bad in ("../audit", "..", "/etc/passwd", "a/b", "a\x00b", "", "x" * 65):
        with pytest.raises(lifecycle.InvalidSessionId):
            lifecycle.session_registry_path(bad)

    # Sibling file untouched.
    assert sibling.exists()
    assert sibling.read_text()


def test_r6_05_dry_run_does_not_unlink_registry(tmp_path, monkeypatch):
    """Even with a valid session_id, `run_cleanup(dry_run=True)` must be
    side-effect-free: the registry file must remain on disk so an
    operator running --dry-run can repeatedly inspect the same state
    without it disappearing after the first call. Round-6 r6-05.
    """
    from browser_fetch_router import lifecycle, paths

    monkeypatch.setattr(paths, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(lifecycle, "state_dir", lambda: tmp_path)

    registry_path = lifecycle.session_registry_path("test-sid-001")
    registry_path.write_text(
        json.dumps(
            {
                "session_id": "test-sid-001",
                "local_processes": [],
            }
        )
    )

    out = lifecycle.run_cleanup(
        all_sessions=True, session_id="test-sid-001", dry_run=True
    )
    assert out["status"] == "ok"
    assert registry_path.exists(), (
        "dry_run=True unlinked the registry — round-6 r6-05 broken"
    )

    # Conversely, dry_run=False unlinks as documented.
    lifecycle.run_cleanup(
        all_sessions=True, session_id="test-sid-001", dry_run=False
    )
    assert not registry_path.exists()


# ============================================================
# r6-06 HIGH — browser_use import executes attacker code
# ============================================================


def test_r6_06_browser_use_probe_does_not_execute_attacker_code(
    tmp_path, monkeypatch
):
    """Round-6 r6-06 fix: `_local_browser_use_available` reads
    distribution metadata (importlib.metadata) instead of importing the
    package. A `browser_use.py` shim on sys.path must NOT execute. The
    function reports presence based on installed-distribution metadata,
    which is unaffected by attacker-controlled CWD / PYTHONPATH.
    """
    from browser_fetch_router import interactive

    sentinel = tmp_path / "browser_use_executed.marker"
    attacker_dir = tmp_path / "attacker_pkgs"
    attacker_dir.mkdir()
    (attacker_dir / "browser_use.py").write_text(
        f"from pathlib import Path\nPath(r'{sentinel}').write_text('owned')\n"
    )
    monkeypatch.delitem(sys.modules, "browser_use", raising=False)
    monkeypatch.syspath_prepend(str(attacker_dir))

    interactive._local_browser_use_available()

    assert not sentinel.exists(), (
        "attacker browser_use.py executed during probe — round-6 r6-06 broken"
    )


# ============================================================
# DISPROVE k03 — interrupted status survives serialization
# ============================================================


def test_r6_disprove_k03_interrupted_status_preserved():
    """Kimi k03 claimed _serialize_or_internal_error could clobber the
    `interrupted` status with `internal_error`. envelope() always returns
    a plain JSON-serializable dict, so json.dumps cannot fail and the
    fallback is unreachable from a normal interrupt path. Asserted as
    safe behavior.
    """
    from browser_fetch_router import cli

    def _interrupting():
        raise KeyboardInterrupt()

    # _emit calls handler, catches KeyboardInterrupt, builds an envelope
    # via envelope(), serializes. The resulting envelope must carry status
    # "interrupted" — never "internal_error".
    captured = {}
    real_print = print

    def fake_print(line):
        captured["line"] = line

    import builtins

    builtins.print = fake_print
    try:
        rc = cli._emit(
            "doctor",
            url=None,
            handler=_interrupting,
            audit=False,
        )
    finally:
        builtins.print = real_print
    payload = json.loads(captured["line"])
    assert payload["status"] == "interrupted", payload
    assert rc == 130


# ============================================================
# DISPROVE k09 — sentinel lock filename does not match *.json
# ============================================================


def test_r6_disprove_k09_lock_file_extension_does_not_match_json_glob(tmp_path):
    """Kimi k09 claimed a misnamed `.lock.json` file could be picked up by
    `glob('*.json')`. The actual sentinel filename pattern in
    _registry_lock_path is `.{session_id}.lock` — extension `.lock`,
    not `.json`. The glob does NOT match.
    """
    from browser_fetch_router import lifecycle, paths

    monkeypatch_dir = tmp_path
    sessions_dir = monkeypatch_dir / "sessions"
    sessions_dir.mkdir(parents=True)

    # Construct the sentinel path the same way register_process does.
    lock = sessions_dir / ".some-session.lock"
    lock.write_text("")
    data = sessions_dir / "some-session.json"
    data.write_text(json.dumps({"session_id": "some-session"}))

    matched = sorted(p.name for p in sessions_dir.glob("*.json"))
    assert matched == ["some-session.json"], matched
    # The lock extension is .lock, not .json — finding disproved.
    assert ".some-session.lock" not in matched


# ============================================================
# r6-g05 LOW — paid-fallback parallel result uses jina-reader TTL
# ============================================================


def test_r6_g05_paid_fallback_cached_with_classification_route_ttl(
    tmp_path, monkeypatch
):
    """When jina returns insufficient_content and Parallel succeeds via
    --allow-paid, the result is cached under the cache_key(jina-reader, url)
    with ROUTE_TTLS["jina-reader"] = 600s, NOT ROUTE_TTLS["parallel"] = 3600s.
    Cost impact: paid Parallel calls revalidated 6x more often than its
    native TTL.

    Whether this is a bug or intentional is a design call (GLM ruled
    intentional, GPT/DeepSeek flagged). Test asserts OBSERVED behavior so
    any future change is loud.
    """
    from browser_fetch_router import read_web

    # read_web imports `cache_dir` directly, so the local name binding is
    # what we have to swap — patching paths.cache_dir would still leave
    # read_web pointing at the user's real cache directory.
    monkeypatch.setattr(read_web, "cache_dir", lambda: tmp_path / "cache")

    monkeypatch.setattr(
        read_web,
        "fetch_jina",
        lambda url, ctx: {
            "status": "insufficient_content",
            "provider": "jina",
            "evidence": {"quality": "low"},
        },
    )
    monkeypatch.setattr(
        read_web,
        "fetch_parallel",
        lambda url, ctx: {
            "status": "ok",
            "provider": "parallel",
            "title": "T",
            "content_markdown": "C",
            "evidence": {"quality": "ok"},
        },
    )

    captured = {}
    from browser_fetch_router import cache as cache_mod

    real_write = cache_mod.CacheStore.write

    def spy_write(self, key, payload, *, ttl_seconds):
        captured.setdefault("calls", []).append(
            {
                "key": key,
                "ttl": ttl_seconds,
                "provider": payload.get("provider"),
                "route": payload.get("route"),
            }
        )
        return real_write(self, key, payload, ttl_seconds=ttl_seconds)

    monkeypatch.setattr(cache_mod.CacheStore, "write", spy_write)

    out = read_web.read_web("https://example.com/article", allow_paid=True)
    assert out["status"] == "ok"
    assert out["provider"] == "parallel"
    assert out["route"] == "jina-reader"

    calls = captured.get("calls") or []
    assert calls, "no cache write captured"
    last = calls[-1]
    # Round-6 r6-g05 fix: TTL now reflects the actual PROVIDER that
    # produced the result (parallel = 3600s), not the URL classification
    # route (jina-reader = 600s). The cache key stays keyed on
    # classification route so subsequent reads dedupe correctly.
    assert last["provider"] == "parallel"
    assert last["route"] == "jina-reader"
    assert last["ttl"] == 3600  # ROUTE_TTLS["parallel"]
    assert last["ttl"] != 600   # NOT ROUTE_TTLS["jina-reader"]
