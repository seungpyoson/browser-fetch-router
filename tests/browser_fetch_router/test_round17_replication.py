"""Round-17 replication tests — TDD-red reproductions of external-review
findings.

Each test asserts the FIXED behavior, so it fails today (proving the
bug exists at the cited surface) and passes after the class-level fix
lands. After the round closes, these tests stay as regression
guarantees: re-introducing the bug fails the same assertion.

Findings under reproduction:

| Class | Reviewers | Severity | Surface |
|-------|-----------|----------|---------|
| A | GPT, DeepSeek, GLM, Kimi (4/4) | P2 | audit.SECRET_TEXT_PATTERNS missing modern token formats |
| B | GPT, DeepSeek, GLM, Kimi (4/4) | P2 | http transport static guard misses http.client/socket bypasses |
| C | DeepSeek, Kimi | P2 | persistence static guard misses json.loads(<bytes-read>) |
| D | DeepSeek, Kimi | P1/P2 | install-agent --adapter-path overwrites arbitrary user files |
| E | Kimi | P1 | CostLedger.reserve(amount=NaN) bypasses cap checks |
| G | GPT | P3 | cache reader returns non-dict envelope to caller |
| H | GLM | P2 | cost.db SQLite store unregistered → 0o644 permissions |
| I | Kimi | P2 | session.current_session_id() returns env var without grammar gate |

Class F (hostname canonicalization SSOT) is intentionally omitted —
analysis showed the IDN homograph case requires Unicode confusables
detection (out of scope for this PR), and the SSOT-divergence case is
unreachable today (no non-ASCII patterns in default_deny). Documented
in the round-17 review summary.
"""

from __future__ import annotations

import json
import math
import re
import stat
import time
from pathlib import Path

import pytest

from browser_fetch_router.audit import REDACTED, sanitize_audit_input
from browser_fetch_router.cache import CacheStore
from browser_fetch_router.cost import CostLedger
from browser_fetch_router.install_agent import install_agent
from browser_fetch_router.schema import SCHEMA_VERSION

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_http_transport_contract import find_http_transport_offenders  # noqa: E402
from test_persistence_contract import find_persistence_offenders  # noqa: E402


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Point HOME and config-derived dirs at an isolated tmp tree."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("BFR_SESSION_ID", raising=False)
    return tmp_path


# ============================================================
# Class A — modern secret token formats redacted by audit
# ============================================================
#
# Today: SECRET_TEXT_PATTERNS misses sk_live_/sk_test_ (Stripe),
# github_pat_ (GitHub fine-grained), glpat- (GitLab), SG. (Sendgrid),
# AIza (Google API), Twilio SK[hex32]. An audit input containing any
# of these emits the plaintext token verbatim into audit.jsonl.

# Each entry is a (token-format-name, sample-token) pair. Samples are
# synthetic — they match the publicly-documented prefix/length/charset
# of each format but are NOT real keys.
MODERN_SECRET_TOKENS = [
    ("stripe-live", "sk_" + "live_" + "x" * 24),
    ("stripe-test", "sk_" + "test_" + "x" * 24),
    ("github-fine-grained-pat", "github_pat_11ABCDEFG_0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJ"),
    ("gitlab-pat", "glpat-abcdefghijklmnopqrst"),
    (
        "sendgrid",
        "SG.abcdefghij1234567890ab.cdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJKLMN",
    ),
    ("google-api-key", "AIzaSy" + "x" * 33),
    ("twilio-key-sid", "SK" + "0" * 32),
]


@pytest.mark.parametrize("token_name,token", MODERN_SECRET_TOKENS, ids=lambda x: x if isinstance(x, str) else x)
def test_class_a_modern_secret_formats_redacted(token_name, token):
    """Each modern token format must be redacted from non-URL audit input.

    Today fails for every token in MODERN_SECRET_TOKENS — the existing
    SECRET_TEXT_PATTERNS only catches Bearer, sk- (OpenAI), xox-,
    JWT eyJ..., AKIA, ghp_, ghs_. Class fix: extend the pattern set
    to cover all formats above (driven by this corpus).
    """
    haystack = f"some prefix key={token} some suffix"
    redacted = sanitize_audit_input(haystack)
    assert REDACTED in redacted, f"{token_name}: redaction never fired"
    assert token not in redacted, f"{token_name}: token leaked through"


# ============================================================
# Class B — HTTP transport static guard catches all bypasses
# ============================================================
#
# Today: BANNED_IMPORT_MODULES = {urllib.request, requests}. So
# `from http.client import HTTPConnection` and `from socket import
# create_connection` slip through (their `node.module` isn't banned,
# and the chain check sees a bare `Name(id=...)` Call, not an
# Attribute chain). `socket.socket()` constructor is also unbanned.
#
# Class fix: per-name banning for multi-purpose modules (socket,
# http.client) so legitimate uses (`socket.getaddrinfo` in cdp.py)
# stay allowed while bypass primitives are caught. Plus
# `socket.socket` in the chain set.


HTTP_BYPASS_OFFENDERS = [
    pytest.param(
        "from http.client import HTTPConnection\nc = HTTPConnection('example.com')",
        "http.client.HTTPConnection",
        id="from-http-client-importfrom",
    ),
    pytest.param(
        "from http.client import HTTPSConnection\nc = HTTPSConnection('example.com')",
        "http.client.HTTPSConnection",
        id="from-http-client-https-importfrom",
    ),
    pytest.param(
        "from socket import create_connection\ns = create_connection(('example.com', 80))",
        "socket.create_connection",
        id="from-socket-create_connection-importfrom",
    ),
    pytest.param(
        "import socket\ns = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\ns.connect(('169.254.169.254', 80))",
        "socket.socket",
        id="socket-socket-constructor-attribute",
    ),
    pytest.param(
        "from socket import socket\ns = socket()",
        "socket.socket",
        id="from-socket-socket-importfrom",
    ),
]


@pytest.mark.parametrize("source,expected_marker", HTTP_BYPASS_OFFENDERS)
def test_class_b_http_static_guard_catches_bypass(tmp_path, source, expected_marker):
    """Each known bypass to SafeHttpClient is detected by the static guard.

    Builds a synthetic 1-file package, runs the production
    `find_http_transport_offenders` against it, asserts the offender
    list mentions the bypass primitive. Today fails for every
    parametrize case — those primitives are unbanned. Class fix:
    extend HTTP_BANNED_* sets in test_http_transport_contract.py.
    """
    pkg = tmp_path / "fake_pkg"
    pkg.mkdir()
    # Need the transport-module exemption file so the scanner has
    # something to ignore; otherwise the bypass would be allowed
    # because every file is treated as transport.
    (pkg / "http_client.py").write_text("# transport module\n")
    (pkg / "bypass.py").write_text(source)

    offenders = find_http_transport_offenders(pkg)
    assert any(expected_marker in entry for entry in offenders), (
        f"static guard missed {expected_marker!r} bypass.\n"
        f"source:\n{source}\n"
        f"offenders={offenders}"
    )


# ============================================================
# Class C — persistence static guard catches read_bytes bypass
# ============================================================
#
# Today: PERSISTENCE_RAW_LOADS_TYPED_RES has only the read_text regex.
# `json.loads(path.read_bytes())` followed by typed access slips
# through (json.loads accepts bytes since Python 3.6).
#
# Class fix: extend the regex tuple to cover read_bytes, and any open(
# ).read() form a future maintainer might reach for.

PERSISTENCE_BYPASS_OFFENDERS = [
    pytest.param(
        '''
import json
from pathlib import Path
def load(p: Path) -> dict:
    data = json.loads(p.read_bytes())
    return data.get("scopes", [])
''',
        "json.loads",
        id="json-loads-read-bytes",
    ),
]


@pytest.mark.parametrize("source,expected_marker", PERSISTENCE_BYPASS_OFFENDERS)
def test_class_c_persistence_static_guard_catches_bypass(tmp_path, source, expected_marker):
    """`json.loads(path.read_bytes())` + typed access must be caught.

    Today fails — the regex only matches read_text. After fix the
    same offending source trips the guard.
    """
    pkg = tmp_path / "fake_pkg"
    pkg.mkdir()
    (pkg / "paths.py").write_text("# helpers module\n")
    (pkg / "bypass.py").write_text(source)

    offenders = find_persistence_offenders(pkg)
    assert any(expected_marker in entry for entry in offenders), (
        f"persistence guard missed read_bytes bypass.\noffenders={offenders}"
    )


# ============================================================
# Class D — agent-channel filesystem write containment
# ============================================================
#
# Today: install_agent("claude", adapter_path="/path/to/.bashrc")
# overwrites .bashrc with the SKILL.md template — destination_for
# accepts any non-directory path verbatim, atomic_write_bytes follows
# through. Same shape for screenshot_tab `--output`.
#
# Class fix: introduce containment validation that requires the
# basename to be `SKILL.md` for adapter installs (the only operation
# install_agent should ever perform), reject symlinks, and reject any
# parent-component traversal.


def test_class_d_install_agent_rejects_non_skill_md_adapter_path(isolated_home):
    """Adapter install must NOT overwrite arbitrary user files.

    Reproduction: with a real bashrc-like file present, request
    install_agent with `adapter_path` pointing at it. Today: file is
    overwritten with SKILL.md template. After fix: install_agent
    returns tool_setup_failed and bashrc bytes are unchanged.
    """
    bashrc = isolated_home / ".bashrc"
    legitimate = b"# legitimate user bashrc\nexport PATH=/usr/local/bin:$PATH\n"
    bashrc.write_bytes(legitimate)

    result = install_agent("claude", force=True, adapter_path=str(bashrc))

    assert result["status"] == "tool_setup_failed", (
        f"install_agent accepted hostile path: {result}"
    )
    assert bashrc.read_bytes() == legitimate, "bashrc was overwritten"


def test_class_d_install_agent_rejects_traversal_adapter_path(isolated_home):
    """Adapter install must reject `..`-laden paths.

    Today: destination_for accepts the path verbatim. After fix:
    paths with `..` segments and a non-SKILL.md basename are
    rejected at the boundary.
    """
    (isolated_home / "subdir").mkdir()
    bashrc = isolated_home / ".bashrc"
    bashrc.write_bytes(b"# legit\n")
    # Give the CLI a string path that traverses out of subdir/ into
    # HOME/.bashrc. Pathlib treats `..` as a literal component until
    # resolved — destination_for's expanduser doesn't normalize, so
    # the validator sees ".bashrc" basename via Path.name and rejects
    # on basename != "SKILL.md".
    sneaky = str(isolated_home / "subdir" / ".." / ".bashrc")

    result = install_agent("claude", force=True, adapter_path=sneaky)
    assert result["status"] == "tool_setup_failed"
    assert bashrc.read_bytes() == b"# legit\n", "bashrc was overwritten"


def test_class_d_basename_rejection_holds_even_with_symlinked_parent(isolated_home):
    """Basename check is the security boundary, regardless of symlinks.

    F-17c (round-17 followup). The original round-17 test asserted
    that any parent symlink rejects the install — but that was
    over-broad defense-in-depth that broke `/tmp` on macOS without
    any security gain. The actual security property: basename
    `.bashrc` is rejected even when reached through a symlinked
    parent directory.
    """
    real_dir = isolated_home / "elsewhere"
    real_dir.mkdir()
    bashrc = real_dir / ".bashrc"
    bashrc.write_bytes(b"# real bashrc\n")

    # Symlink an "innocent-looking" parent that resolves to elsewhere.
    sneaky_parent = isolated_home / "skills_alias"
    sneaky_parent.symlink_to(real_dir)
    target = sneaky_parent / ".bashrc"

    result = install_agent("claude", force=True, adapter_path=str(target))
    assert result["status"] == "tool_setup_failed", (
        f"basename .bashrc must reject regardless of symlinked parent: {result}"
    )
    assert bashrc.read_bytes() == b"# real bashrc\n"


def test_class_d_symlinked_parent_with_skill_md_basename_works(isolated_home):
    """Symlinked parent + valid SKILL.md basename must succeed.

    F-17c (round-17 followup). `os.replace` does not follow symlinks
    for the destination — replacing a target through a symlinked
    parent just lands the file at the resolved location. No
    security boundary is crossed because the basename is SKILL.md.
    Operators who organize agent skills under symlinks must not be
    blocked.

    F-N4 (round-17 followup-2 review): strengthened from the original
    F-17c version, which only asserted the integration-level error code
    was NOT `"invalid_adapter_path"` — a negation-style assertion that
    silently passes if the error code is renamed. The unit-level call
    to `validate_skill_md_dest` directly pins the validator contract;
    the integration check remains as a backstop.
    """
    from browser_fetch_router.paths import (
        UnsafeDestination,
        validate_skill_md_dest,
    )

    real_dir = isolated_home / ".claude" / "skills" / "browser-fetch-router"
    real_dir.mkdir(parents=True)
    sneaky_parent = isolated_home / "skill_alias"
    sneaky_parent.symlink_to(real_dir)
    target = sneaky_parent / "SKILL.md"

    # Unit-level (positive assertion): validator MUST return the path
    # without raising. Renaming an error code elsewhere cannot mask a
    # regression in this assertion.
    try:
        validated = validate_skill_md_dest(target)
    except UnsafeDestination as exc:
        pytest.fail(
            f"validate_skill_md_dest rejected legitimate symlinked-parent "
            f"path with SKILL.md basename: {exc}"
        )
    assert validated.name == "SKILL.md"

    # Integration-level backstop: install_agent must not short-circuit at
    # path validation. Downstream subprocess steps may fail in this
    # isolated tree (no real CLI to exec); that's outside this test's
    # scope — only the path-validation envelope is rejected.
    result = install_agent("claude", force=True, adapter_path=str(target))
    err_code = (result.get("error") or {}).get("code", "")
    assert err_code != "invalid_adapter_path", (
        f"install_agent rejected the symlinked-parent path at the "
        f"validation boundary: {result}"
    )


def test_class_d_validate_image_dest_accepts_macos_tmp():
    """`/tmp/screenshot.png` must validate on macOS.

    F-17a (round-17 followup). On macOS `/tmp` is a system symlink
    to `/private/tmp`. The original round-17 implementation walked
    unresolved parent components and rejected /tmp as a symlink,
    breaking the documented operator scratch path.

    Skips on systems where /tmp doesn't exist or isn't a symlink
    (Linux). On those systems the test is trivially correct anyway.
    """
    from browser_fetch_router.paths import UnsafeDestination, validate_image_dest

    if not Path("/tmp").exists():
        pytest.skip("/tmp doesn't exist on this system")

    try:
        validated = validate_image_dest("/tmp/screenshot.png")
    except UnsafeDestination as exc:
        pytest.fail(
            f"validate_image_dest rejected /tmp/screenshot.png — "
            f"the contract claims this works. Error: {exc}"
        )
    assert validated.name == "screenshot.png"


# ============================================================
# Class E — numeric input validation (NaN/Inf)
# ============================================================
#
# Today: CostLedger.reserve(amount=float("nan")) passes both
# `amount < 0` and `amount > request_cap` checks (NaN comparisons all
# return False), then inserts a NaN cost row into SQLite, bypassing
# every cap.
#
# Class fix: validate `math.isfinite(amount)` (and is_finite for
# every numeric input) at function boundary — raise or return False
# loud.


@pytest.mark.parametrize("bad_amount", [float("nan"), float("inf"), float("-inf")])
def test_class_e_reserve_rejects_non_finite_amount(tmp_path, bad_amount):
    """`reserve(amount=NaN/Inf/-Inf)` must NOT bypass cap checks.

    Today: returns the audit_id (truthy) → caller proceeds with paid
    call → ledger contains a NaN row that corrupts subsequent SUMs.
    After fix: returns False (or raises) before any DB mutation.
    """
    ledger = CostLedger(tmp_path / "cost.db")
    result = ledger.reserve(
        "session",
        "fxtwitter",
        bad_amount,
        request_cap=1.0,
        session_cap=10.0,
        daily_cap=10.0,
    )
    assert result is False, (
        f"reserve({bad_amount}) returned {result!r} — non-finite amount "
        f"bypassed cap checks. Subsequent session_total may be polluted."
    )


def test_class_e_reserve_rejects_non_finite_caps(tmp_path):
    """Non-finite cap parameters are operator config bugs, fail loud.

    A NaN session_cap would let `session_total + amount > session_cap`
    silently pass for any amount.
    """
    ledger = CostLedger(tmp_path / "cost.db")
    with pytest.raises((ValueError, TypeError)):
        ledger.reserve(
            "session",
            "fxtwitter",
            0.001,
            request_cap=float("nan"),
            session_cap=10.0,
            daily_cap=10.0,
        )


# ============================================================
# Class G — cache nested-shape validation
# ============================================================
#
# Today: CacheStore.read returns whatever sits under `envelope` as
# long as the outer dict has the expected keys. A planted file with
# `envelope: "string"` returns a str to the caller, which then
# AttributeError's on `.get(...)`. The persistence contract guards
# top-level shape but doesn't reach into nested record schema.
#
# Class fix: validate envelope is a dict, expires_at is numeric,
# schema_version is the exact constant. On invalid nested shape,
# back up and return None (cache miss).


def test_class_g_cache_returns_none_for_non_dict_envelope(tmp_path):
    """Cache record with non-dict envelope must be a miss, not a crash.

    Today: returns the str envelope; caller crashes downstream.
    After fix: returns None and (ideally) backs the corrupt file up.
    """
    store = CacheStore(tmp_path / "cache")
    key = "abcd1234567890"
    path = tmp_path / "cache" / key[:2] / f"{key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            "schema_version": SCHEMA_VERSION,
            "expires_at": time.time() + 3600,
            "envelope": "not-a-dict",
        })
    )

    result = store.read(key)
    assert result is None, (
        f"cache returned {result!r} for non-dict envelope — caller "
        f"will AttributeError on .get(...)"
    )


def test_class_g_cache_returns_none_for_non_numeric_expires_at(tmp_path):
    """Cache record with non-numeric expires_at must be a miss.

    Today: `data.get('expires_at', 0) < time.time()` does a string-
    vs-float comparison which raises TypeError on Python 3.
    """
    store = CacheStore(tmp_path / "cache")
    key = "abcd1234567890"
    path = tmp_path / "cache" / key[:2] / f"{key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            "schema_version": SCHEMA_VERSION,
            "expires_at": "tomorrow",
            "envelope": {"ok": True},
        })
    )

    # Should be a miss, not a crash.
    assert store.read(key) is None


# ============================================================
# Class H — SQLite store registered in persistence contract
# ============================================================
#
# Today: cost.db is created via sqlite3.connect() at the umask default
# (typically 0o644). The parent directory is mkdir()'d without
# ensure_private_dir. Persistence-contract invariant E (0o600) doesn't
# fire because cost.db isn't in OBJECT_STORES.
#
# Class fix: route cost.db creation through ensure_private_dir + chmod
# 0o600 on init, AND register cost.db in a SQLITE_STORES bucket so the
# contract test parametrizes over it.


def test_class_h_cost_db_permissions_are_0o600(tmp_path):
    """cost.db file must be 0o600 (matching every other persistent store)."""
    ledger = CostLedger(tmp_path / "subdir" / "cost.db")
    # Trigger schema init / first write.
    ledger.reserve(
        "s",
        "fxtwitter",
        0.001,
        request_cap=1.0,
        session_cap=1.0,
        daily_cap=1.0,
    )
    db_path = tmp_path / "subdir" / "cost.db"
    mode = stat.S_IMODE(db_path.stat().st_mode)
    assert mode == 0o600, (
        f"cost.db permissions are 0o{mode:o}, expected 0o600. "
        f"Same-user processes can read cost history outside the CLI flow."
    )


def test_class_h_cost_db_parent_is_0o700(tmp_path):
    """cost.db parent directory must be 0o700.

    Cross-subsystem invariant from persistence contract E. Today the
    parent is created with plain mkdir(parents=True) (umask default).
    """
    parent = tmp_path / "subdir"
    CostLedger(parent / "cost.db")
    mode = stat.S_IMODE(parent.stat().st_mode)
    assert mode == 0o700, f"cost.db parent dir is 0o{mode:o}, expected 0o700"


# ============================================================
# Class I — session_id grammar gated at the source
# ============================================================
#
# Today: session.current_session_id() returns os.environ['BFR_SESSION_ID']
# verbatim. Lifecycle.session_registry_path() validates against the
# grammar but other consumers (approvals.add_approval) accept any
# string. Trust-boundary mismatch.
#
# Class fix: validate at the source. current_session_id() raises
# InvalidSessionId when the env var fails the grammar.


def test_class_i_current_session_id_rejects_path_traversal_env(monkeypatch):
    """BFR_SESSION_ID with grammar-violating value must fail at the source.

    Today: returns "../../etc/evil" verbatim, propagates to every
    consumer (approvals, audit) without validation. After fix:
    `current_session_id()` raises InvalidSessionId, the CLI surfaces
    a usage_error envelope.
    """
    from browser_fetch_router.lifecycle import InvalidSessionId
    from browser_fetch_router.session import current_session_id

    monkeypatch.setenv("BFR_SESSION_ID", "../../etc/evil")
    with pytest.raises(InvalidSessionId):
        current_session_id()


@pytest.mark.parametrize(
    "bad_value",
    [
        "../../etc/passwd",
        "session/with/slash",
        "with space",
        "a" * 65,  # over 64-char limit
        "",  # empty
        # Note: an embedded null byte cannot reach this code path —
        # POSIX rejects null bytes in environment variable values
        # before the program ever sees them. So `\0` doesn't need a
        # test case here; it's already structurally impossible.
    ],
)
def test_class_i_current_session_id_rejects_grammar_violations(monkeypatch, bad_value):
    """Every grammar violation surfaced via env var must raise."""
    from browser_fetch_router.lifecycle import InvalidSessionId
    from browser_fetch_router.session import current_session_id

    monkeypatch.setenv("BFR_SESSION_ID", bad_value)
    with pytest.raises(InvalidSessionId):
        current_session_id()


# ============================================================
# Round-17 followup-2 — F-N findings (internal adversarial review)
# ============================================================
#
# F-N1 + F-N2: doc/comment drift. F-17a removed `_reject_symlink_on_path`
# from the validators and updated W4 in cli-write-containment-contract.md
# to "symlinks NOT rejected". But four other prose mentions in the same
# doc, and two code comments at the validator call sites, still claimed
# symlinks were part of validation. A reviewer reading end-to-end gets
# contradictory guidance, and the maintenance section actively
# misdirects anyone adding a new write operation per the contract's
# own playbook.
#
# Class of problem: single source of truth violation (Rule 6). The W4
# invariant is restated in multiple locations; updating one leaves the
# others stale silently. Class fix: invariants live in W4 (the contract
# doc) and the validator docstring; supporting code comments and other
# doc sections must NOT restate them. The mechanical lock below
# enforces the no-restatement discipline for the specific stale phrases
# F-N1 surfaced.

REPO_ROOT = Path(__file__).resolve().parents[2]

# Phrases that contradicted W4 ("symlinks NOT rejected") in the round-17
# closing-pass commit. Each is the literal substring as it appeared in
# the partially-updated doc; matching as a substring (not as a regex)
# keeps the test resistant to legitimate W4 prose that uses similar
# words.
SYMLINK_DRIFT_FORBIDDEN = [
    "(basename, extension, symlink-on-path)",
    "(basename, parent symlinks)",
    "extension/basename check + symlink-on-path rejection",
    "symlink-on-path, traversal-via-dot-dot",
]


def test_followup_F_N1_cli_write_containment_doc_no_stale_symlink_phrases():
    """W4 says symlinks are NOT rejected; the rest of the doc must agree.

    F-17a removed `_reject_symlink_on_path` from the code but left four
    supporting prose mentions that still claimed symlink-on-path was
    part of validation:

      - the intro "validator design" sentence
      - the threat-model "still catches the obvious cases" sentence
      - the maintenance "exercise: ..., symlink-on-path, ..." sentence
      - the maintenance "extension/basename check + symlink-on-path
        rejection" sentence

    Markdown wraps prose at ~78 cols, so a forbidden phrase may straddle
    a line break (`(basename, extension,\\nsymlink-on-path)`). Normalize
    runs of whitespace before substring search so wrapped forms are
    caught.

    Lock the contract so no future partial update reintroduces them.
    """
    doc = (
        REPO_ROOT
        / "docs"
        / "browser-fetch-router-cli-write-containment-contract.md"
    ).read_text()
    normalized = re.sub(r"\s+", " ", doc)
    offenders = [
        phrase for phrase in SYMLINK_DRIFT_FORBIDDEN if phrase in normalized
    ]
    assert not offenders, (
        f"stale phrase(s) in cli-write-containment-contract.md contradict "
        f"W4 (symlinks NOT rejected). F-17a removed the "
        f"_reject_symlink_on_path walk; supporting prose must agree. "
        f"Stale phrases still present: {offenders}"
    )


@pytest.mark.parametrize(
    "module_path",
    [
        "browser_fetch_router/install_agent.py",
        "browser_fetch_router/read_user_tabs.py",
    ],
)
def test_followup_F_N2_no_stale_symlink_rejection_comment(module_path):
    """Code comments must not claim "rejects symlinks on the path".

    The validators no longer do that, and the comment misleads readers.
    F-17a removed `_reject_symlink_on_path` but two call-site comments
    were not updated. The validator docstrings are the source of truth;
    call-site comments should not restate the invariant text.

    Comments wrap, so the literal phrase may be split across lines as
    `# ... rejects\n    # symlinks ...`. Normalize comment-line
    continuations before substring search so the wrapped form is also
    caught.
    """
    src = (REPO_ROOT / module_path).read_text()
    normalized = re.sub(r"\n\s*#\s*", " ", src)
    assert "rejects symlinks" not in normalized, (
        f"{module_path} contains stale comment 'rejects symlinks' that "
        f"contradicts paths.validate_*_dest (which no longer reject "
        f"symlinks; see W4 in cli-write-containment-contract.md)."
    )
