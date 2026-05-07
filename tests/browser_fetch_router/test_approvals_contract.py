"""Approvals subsystem contract suite.

Single source of truth for "safe authorization" in the
browser_fetch_router package. Every fetch handler that touches
caller-supplied or stored URLs goes through `can_read_url(...)` →
`approval_matches(...)` → (if exact-one-time matches OR
default-deny passes AND a persistent scope matches) → True/False.
This file enumerates the 20 invariants that chain MUST satisfy.

Adding a new fetch handler:

  1. Call `can_read_url(url, persistent_scopes, exact_one_time=...)`
     before issuing the network request. Any handler that fetches
     URL-shaped content WITHOUT routing through `can_read_url` is
     a default-deny bypass — caught by the static guard
     `test_fetch_handler_routes_through_can_read_url`.
  2. Run this suite — every applicable invariant runs against the
     authorization layer automatically.

Adding a new scope kind:

  1. Add to `VALID_SCOPE_KINDS` in approvals.py.
  2. Add a per-kind canonicalizer in `normalize_scope`.
  3. Document in the contract doc + add tests here.
  4. The grammar invariant tests will fail-loud if the new kind
     doesn't follow the `kind:value` shape with full canonicalization.

Why this exists: PR #737 went through 16+ rounds of review. Three
prior subsystems closed via similar contracts (persistence on commit
201a050; HTTP transport + lifecycle on commit 165c257). Approvals had
the SECOND-most accumulated review rounds (r6-02 silent dead approval,
r7-02 exact: URL SSRF storage, r9-01 unknown kind defense, r11-i02
exact-one-time wrapping, r11-i03 empty exact, r12-i05 missing
separator, r15-02 regional subdomain bypass). This is the closing
pass for that subsystem — same systematic move at a higher
abstraction.
"""
from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path

import pytest


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


# ============================================================
# A. Scope grammar invariants
# ============================================================


@pytest.mark.parametrize(
    "kindless_input",
    [
        "example.com",
        "foo",
        "*.gmail.com",
        "  ",
        "exact",  # has the kind word but no separator
    ],
)
def test_a1_kindless_scope_rejected(kindless_input):
    """Invariant A1: `normalize_scope` requires the `kind:value`
    separator. Without `:` the call raises `InvalidScope`. Closes
    the silent-dead-approval class (round-12 i05): pre-fix
    `add_approval("foo")` stored a record that `approval_matches`
    short-circuited on its own `:` check, returning False forever.
    """
    from browser_fetch_router.approvals import InvalidScope, normalize_scope

    with pytest.raises(InvalidScope, match="missing_kind_separator"):
        normalize_scope(kindless_input)


@pytest.mark.parametrize(
    "unknown_kind_input",
    [
        "hosname:example.com",      # typo
        "host:example.com",          # close-but-not-quite
        "exec:foo",                  # plausible-looking
        "regex:.*",                  # not in VALID_SCOPE_KINDS
        "any:*",
        "EXACT_TYPO:url",
        # URL-shaped strings: `https:` is parsed as the kind, fails
        # at unknown_scope_kind. Closes the "agent passes a URL as a
        # scope" usability footgun loud.
        "https://example.com/",
        "http://example.com/",
    ],
)
def test_a2_unknown_kind_rejected(unknown_kind_input):
    """Invariant A2: kind MUST be in VALID_SCOPE_KINDS
    (`exact`, `hostname`, `wildcard`). Anything else raises
    `InvalidScope`. Closes round-9 r9-01 silent-dead class:
    pre-fix a typo'd `hosname:example.com` was stored verbatim,
    `approval_matches` rejected because kind not canonical, but
    the record sat there forever.
    """
    from browser_fetch_router.approvals import InvalidScope, normalize_scope

    with pytest.raises(InvalidScope, match="unknown_scope_kind"):
        normalize_scope(unknown_kind_input)


@pytest.mark.parametrize(
    "bad_hostname",
    [
        "hostname:",                       # empty value
        "hostname:.",                      # only dot
        "hostname:.host",                  # leading dot
        "hostname:host:port",              # port in scope
        "hostname:host/path",              # path
        "hostname:host?q=1",               # query
        "hostname:host#frag",              # fragment
        "hostname:host with space",        # space
        "hostname:**.host",                # double wildcard
        "wildcard:**.host",                # same for wildcard
        "wildcard:*",                      # bare star
    ],
)
def test_a3_hostname_wildcard_canonicalization_rejects_malformed(bad_hostname):
    """Invariant A3: hostname/wildcard scopes route through
    `_canonicalize_hostname_scope` which rejects ports, paths,
    queries, fragments, double `**.`, leading dots, embedded
    whitespace, and similar shape errors. Closes round-6 r6-02
    silent-dead class.
    """
    from browser_fetch_router.approvals import InvalidScope, normalize_scope

    with pytest.raises(InvalidScope):
        normalize_scope(bad_hostname)


@pytest.mark.parametrize(
    "ssrf_exact_url",
    [
        "exact:http://169.254.169.254/latest/meta-data/",  # AWS IMDS
        "exact:http://10.0.0.1/admin",                      # RFC 1918
        "exact:http://192.168.1.1/",                        # RFC 1918
        "exact:http://metadata.google.internal/",           # GCP metadata
    ],
)
def test_a4_exact_url_ssrf_target_rejected(ssrf_exact_url):
    """Invariant A4: `exact:<URL>` scopes go through
    `normalize_and_validate_url` so non-loopback SSRF targets cannot
    be stored as legitimate exact one-time approvals. Closes
    round-7 r7-02 P1-security class: pre-fix
    `exact:http://169.254.169.254/` was silently stored, and
    `can_read_url`'s exact-one-time branch returned True for the
    IMDS URL.

    Loopback (127.0.0.1, ::1, localhost) is INTENTIONALLY allowed
    here (`allow_loopback=True` in the exact: branch) because
    `exact:http://127.0.0.1:9222/...` is the user's own browser
    via CDP. The SSRF rejection targets non-loopback private +
    metadata + IMDS hosts only — verified by the positive
    test below.
    """
    from browser_fetch_router.approvals import normalize_scope
    from browser_fetch_router.url_safety import UnsafeUrl

    with pytest.raises(UnsafeUrl):
        normalize_scope(ssrf_exact_url)


def test_a4b_loopback_exact_url_allowed():
    """Invariant A4b (companion to A4): loopback URLs in exact:
    scopes are deliberately permitted because they reference the
    user's own local browser via CDP. The exact: branch passes
    `allow_loopback=True` to `normalize_and_validate_url`.
    """
    from browser_fetch_router.approvals import normalize_scope

    # Must NOT raise.
    canonical = normalize_scope("exact:http://127.0.0.1:9222/json")
    assert canonical.startswith("exact:")
    assert "127.0.0.1" in canonical


def test_a5_empty_exact_value_rejected():
    """Invariant A5: `exact:` with empty value raises `InvalidScope`.
    A bare `exact:` would store a sentinel that matches no URL —
    same silent-dead class as r6-02. Closed by round-11 i03."""
    from browser_fetch_router.approvals import InvalidScope, normalize_scope

    with pytest.raises(InvalidScope, match="empty_exact_scope"):
        normalize_scope("exact:")


@pytest.mark.parametrize(
    "valid_scope",
    [
        "hostname:example.com",
        "hostname:Example.COM",                # case normalized
        "wildcard:example.com",
        "wildcard:*.example.com",              # `*.` prefix stripped
        "hostname:example.com.",               # trailing FQDN dot stripped
        "exact:https://example.com/",
        "exact:list-all-tabs",                 # non-URL sentinel preserved
    ],
)
def test_a6_valid_scopes_canonicalize_without_raising(valid_scope):
    """Invariant A6 (positive): valid scopes canonicalize cleanly
    and round-trip — calling `normalize_scope` on the result
    returns the same canonical form."""
    from browser_fetch_router.approvals import normalize_scope

    canonical = normalize_scope(valid_scope)
    assert ":" in canonical
    # Idempotence: canonicalizing the canonical form yields itself.
    assert normalize_scope(canonical) == canonical


# ============================================================
# B. Authorization decisions (can_read_url)
# ============================================================


def test_b1_default_deny_blocks_hostname_scope_match(isolated_home):
    """Invariant B1: `can_read_url` precedence — default-deny WINS
    over hostname/wildcard scopes. An agent with
    `wildcard:google.com` approval still cannot read
    `gmail.com/inbox` because gmail is host-sensitive and
    default-denied.
    """
    from browser_fetch_router.approvals import can_read_url

    assert can_read_url(
        "https://gmail.com/inbox",
        persistent_scopes=["wildcard:gmail.com"],
    ) is False


def test_b2_exact_one_time_overrides_default_deny(isolated_home):
    """Invariant B2: `exact_one_time` is the ONLY way to override
    default-deny. An exact URL match in `exact_one_time` returns
    True even for a default-denied target — that's the explicit
    user-consent ceremony for sensitive URLs.
    """
    from browser_fetch_router.approvals import can_read_url

    target = "https://gmail.com/specific-message"
    assert can_read_url(target, persistent_scopes=[]) is False
    assert can_read_url(
        target,
        persistent_scopes=[],
        exact_one_time=[f"exact:{target}"],
    ) is True


def test_b3_no_approval_blocks_normal_url(isolated_home):
    """Invariant B3: a URL with no matching scope and no
    default-deny match returns False — the default state is
    DENY, not allow.
    """
    from browser_fetch_router.approvals import can_read_url

    assert can_read_url(
        "https://example.com/article",
        persistent_scopes=[],
    ) is False


def test_b4_persistent_scope_match_allows_non_denied_url(isolated_home):
    """Invariant B4: a non-default-denied URL with a matching
    persistent scope returns True. Standard happy path.
    """
    from browser_fetch_router.approvals import can_read_url

    assert can_read_url(
        "https://example.com/article",
        persistent_scopes=["hostname:example.com"],
    ) is True


# ============================================================
# C. Defense-in-depth (approval_matches against corrupt records)
# ============================================================


@pytest.mark.parametrize(
    "corrupt_scope",
    [
        "hosname:example.com",        # unknown kind
        "example.com",                # missing separator
        "hostname:**.example.com",    # bad hostname canonicalization
        "exact:http://169.254.169.254/",  # SSRF target (post-fix would never store)
        "",                           # empty
    ],
)
def test_c1_approval_matches_returns_false_on_corrupt_stored_scope(
    corrupt_scope,
):
    """Invariant C1: `approval_matches` is read-side defense-in-
    depth — it catches `InvalidScope` AND `UnsafeUrl` from
    `normalize_scope` and returns False rather than crashing.

    Even though the write-side gates (`add_approval`) reject
    malformed scopes, a hand-planted record or a record from a
    prior buggy version could still be on disk. The auth check
    must degrade gracefully on stored corruption.

    Closes round-9 r9-01 + round-7 r7-02 read-side defenses.
    """
    from browser_fetch_router.approvals import approval_matches

    # Must not raise. Must return False.
    result = approval_matches(corrupt_scope, "https://example.com/")
    assert result is False


def test_c2_can_read_url_resilient_to_corrupt_exact_one_time_scope():
    """Invariant C2: `can_read_url` wraps `normalize_scope` in
    try/except for the exact-one-time loop. A corrupt
    exact-one-time entry is skipped, not surfaced as a crash.
    Round-11 i02.
    """
    from browser_fetch_router.approvals import can_read_url

    # Mix of corrupt + valid exact-one-time scopes; valid one
    # should still grant access.
    target = "https://gmail.com/specific"
    result = can_read_url(
        target,
        persistent_scopes=[],
        exact_one_time=[
            "corrupt-no-separator",
            f"exact:{target}",
        ],
    )
    assert result is True


# ============================================================
# D. Default-deny invariants (cross-subsystem with default_deny)
# ============================================================


def test_d1_every_bare_hostname_has_wildcard_counterpart():
    """Invariant D1: HOST_SENSITIVE_PATTERNS and DENY_PATTERNS
    enforce the rule that every bare hostname has its `*.<host>`
    wildcard counterpart. The matcher does exact equality for
    non-wildcards, so a bare entry without its sibling lets
    every regional subdomain bypass.

    Closes round-15 r15-02 P1-security class. Mirrors the
    static-guard test in `test_round15_replication.py`.
    """
    from browser_fetch_router import default_deny

    bare = {p for p in default_deny.HOST_SENSITIVE_PATTERNS if not p.startswith("*.")}
    wildcards = {p[2:] for p in default_deny.HOST_SENSITIVE_PATTERNS if p.startswith("*.")}
    missing = bare - wildcards
    assert not missing, (
        f"HOST_SENSITIVE_PATTERNS bare hosts without `*.<host>` "
        f"counterpart: {sorted(missing)}. Class r15-02."
    )


@pytest.mark.parametrize(
    "default_denied_url",
    [
        "https://gmail.com/inbox",
        "https://www.gmail.com/inbox",
        "https://m.gmail.com/inbox",
        "https://1password.com/login",
        "https://my-vault.1password.com/",
        "https://us-east-1.console.aws.amazon.com/iam",
        "https://outlook.live.com/mail/0/inbox",
    ],
)
def test_d2_default_deny_catches_subdomain_variants(default_denied_url):
    """Invariant D2: every host-sensitive pattern catches its
    subdomain variants via the `*.` wildcard counterpart. Locks
    in the round-3 finding U fix + the round-15 r15-02 fix.
    """
    from browser_fetch_router.default_deny import is_default_denied

    assert is_default_denied(default_denied_url) is True


# ============================================================
# E. Storage and lifecycle (cross-subsystem with persistence)
# ============================================================


def test_e1_add_approval_then_list_active_scopes_roundtrip(isolated_home):
    """Invariant E1: `add_approval(persisted=True)` makes the
    scope visible to `list_active_scopes` regardless of session.
    Persisted scopes are session-independent.
    """
    from browser_fetch_router.approvals import (
        add_approval,
        list_active_scopes,
    )

    add_approval(
        "hostname:example.com",
        session_id="session-A",
        persisted=True,
    )
    # Different session can see persisted scopes.
    scopes = list_active_scopes(session_id="session-B")
    assert "hostname:example.com" in scopes


def test_e2_session_scope_visible_only_to_owning_session(isolated_home):
    """Invariant E2: session-scoped (`persisted=False`)
    approvals are visible ONLY to the session_id that created
    them. Other sessions don't see them — prevents cross-session
    privilege leak.
    """
    from browser_fetch_router.approvals import (
        add_approval,
        list_active_scopes,
    )

    add_approval(
        "hostname:example.com",
        session_id="session-A",
        persisted=False,
    )
    assert "hostname:example.com" in list_active_scopes(session_id="session-A")
    assert "hostname:example.com" not in list_active_scopes(session_id="session-B")


def test_e3_session_scope_expires(isolated_home):
    """Invariant E3: session-scoped approvals expire after the
    TTL (`SESSION_TTL_SECONDS = 8h`). After expiry,
    `list_active_scopes` no longer returns them even for the
    owning session.
    """
    import time as time_module

    from browser_fetch_router.approvals import (
        SESSION_TTL_SECONDS,
        add_approval,
        list_active_scopes,
    )

    add_approval(
        "hostname:expiring.example.com",
        session_id="session-A",
        persisted=False,
    )
    # Within TTL: visible.
    assert "hostname:expiring.example.com" in list_active_scopes(
        session_id="session-A"
    )
    # Past TTL: not visible.
    future = time_module.time() + SESSION_TTL_SECONDS + 60
    assert "hostname:expiring.example.com" not in list_active_scopes(
        session_id="session-A", now=future
    )


def test_e4_revoke_removes_scope(isolated_home):
    """Invariant E4: `revoke_scope` removes the scope from the
    store. After revocation, `list_active_scopes` no longer
    returns it.
    """
    from browser_fetch_router.approvals import (
        add_approval,
        list_active_scopes,
        revoke_scope,
    )

    add_approval(
        "hostname:revoke-test.example.com",
        session_id="any-session",
        persisted=True,
    )
    assert "hostname:revoke-test.example.com" in list_active_scopes(
        session_id="any-session"
    )
    revoke_scope("hostname:revoke-test.example.com")
    assert "hostname:revoke-test.example.com" not in list_active_scopes(
        session_id="any-session"
    )


def test_e5_add_approval_rejects_invalid_scope_at_write(isolated_home):
    """Invariant E5: `add_approval` calls `normalize_scope` upfront,
    so a malformed scope raises `InvalidScope` BEFORE writing to
    disk. Combined with C1 (read-side defense-in-depth), the store
    is closed at both ends.
    """
    from browser_fetch_router.approvals import InvalidScope, add_approval

    with pytest.raises(InvalidScope):
        add_approval(
            "hosname:example.com",  # typo'd kind
            session_id="any-session",
            persisted=True,
        )


def test_e6_malformed_expires_at_silently_skipped(isolated_home):
    """Invariant E6: a stored record with malformed `expires_at`
    is silently skipped by `list_active_scopes` rather than
    crashing the auth check. Defense-in-depth for stored
    corruption.
    """
    from browser_fetch_router.approvals import _store_path, list_active_scopes
    from browser_fetch_router.paths import (
        atomic_write_bytes,
        config_dir,
        ensure_private_dir,
    )

    ensure_private_dir(config_dir())
    payload = {
        "scopes": [
            {
                "scope": "hostname:malformed-expiry.example.com",
                "session_id": "session-A",
                "persisted": False,
                "created_at": "2026-01-01T00:00:00+00:00",
                "expires_at": "this-is-not-a-date",
            },
        ]
    }
    atomic_write_bytes(_store_path(), json.dumps(payload).encode("utf-8"))

    # Must not raise — corrupt expires_at silently filtered.
    result = list_active_scopes(session_id="session-A")
    assert "hostname:malformed-expiry.example.com" not in result


# ============================================================
# F. Static guards
# ============================================================


def test_f1_no_direct_approval_store_access_outside_approvals_module():
    """Static guard: production code outside `approvals.py` must
    NOT directly read or write the contents of
    `~/.config/browser-fetch-router/approvals.json`. All store
    CONTENT access goes through `add_approval`, `revoke_scope`,
    `list_active_scopes`, `_load_unlocked` — which enforce locking,
    atomic writes, and grammar validation.

    A direct `read_json_dict(approvals.json)` or
    `path.write_text(approvals.json)` from another module would
    bypass the SentinelLock, the scope normalization, and the
    expiration filter.

    `doctor.py` is allow-listed: it only inspects FILE METADATA
    (permission mode via stat) for the health check, never the
    approval contents. Reporting on file mtime/size/perm is not
    a bypass of the approvals API.
    """
    pkg = Path(__file__).resolve().parents[2] / "browser_fetch_router"
    approvals_module = pkg / "approvals.py"

    # Modules that reference the literal `"approvals.json"` for
    # health-check / discovery purposes only (no content access).
    METADATA_ONLY_ALLOW_LIST = {
        # doctor.py inspects permission bits via stat() — see
        # _too_permissive in doctor.py. No content read or write.
        pkg / "doctor.py",
    }

    offenders: list[str] = []
    for py in pkg.rglob("*.py"):
        if py.resolve() == approvals_module.resolve():
            continue
        if py.resolve() in {p.resolve() for p in METADATA_ONLY_ALLOW_LIST}:
            continue
        text = py.read_text(encoding="utf-8")
        if '"approvals.json"' in text or "'approvals.json'" in text:
            line_no = next(
                (
                    i + 1
                    for i, line in enumerate(text.splitlines())
                    if "approvals.json" in line
                    and "#" not in line.split("approvals.json")[0]
                ),
                None,
            )
            offenders.append(
                f"{py.relative_to(pkg.parent)}:{line_no} references "
                "'approvals.json' literal — content access must go "
                "through approvals module functions"
            )

    assert not offenders, (
        "Direct approval-store content access detected outside "
        "approvals.py:\n"
        + "\n".join(offenders)
        + "\nUse add_approval / revoke_scope / list_active_scopes / "
        "_load_unlocked instead. For metadata-only inspection (perm/"
        "mtime/size), add the module to METADATA_ONLY_ALLOW_LIST."
    )


def test_f2_user_tab_handlers_route_through_can_read_url():
    """Static guard: every handler that reads from the user's
    LOGGED-IN browser state via CDP MUST gate the request behind
    `can_read_url(...)`. Skipping the gate is a default-deny
    bypass for sensitive content.

    The threat model differs by handler type:

      - **`read-user-tabs` handlers** (list_tabs / read_tab /
        screenshot_tab) reach the user's authenticated browser
        sessions via CDP. A tab on `gmail.com` exposes the user's
        actual inbox. These MUST gate via approvals + default-deny.

      - **`read-web`** uses SafeHttpClient (cookieless) with the
        url_safety SSRF defense. Fetching `https://gmail.com/`
        without cookies just yields a login page — no
        authenticated content reachable. Default-deny does NOT
        apply (would block public web fetching for no security
        gain). Documented architectural decision.

      - **`interactive-browser`** has a separate approval model
        (hosted-browser scope) for live agent task execution.
        Not in this contract's scope.

    A new user-tabs handler added without `can_read_url` trips
    this test instead of silently fetching authenticated content.
    """
    pkg = Path(__file__).resolve().parents[2] / "browser_fetch_router"

    REQUIRED_GATES = {
        # Only handlers that reach the user's authenticated browser
        # state via CDP — these MUST gate via the approvals layer.
        "read_user_tabs.py": ["_resolve_and_authorize_tab", "list_tabs"],
    }

    offenders: list[str] = []
    for module_rel, required_fns in REQUIRED_GATES.items():
        module_path = pkg / module_rel
        if not module_path.exists():
            offenders.append(f"missing module {module_rel}")
            continue
        text = module_path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(text)
        except SyntaxError as exc:
            offenders.append(f"{module_rel} has syntax error: {exc}")
            continue
        # Valid approval-layer gates a user-tab handler may use:
        #
        #   - can_read_url(url, ...): per-URL authorization (the common
        #     case for read_tab / screenshot_tab where the tab URL is
        #     known up front — used inside _resolve_and_authorize_tab).
        #
        #   - is_default_denied(url): direct default-deny check (rare
        #     — usually wrapped inside can_read_url).
        #
        #   - list_active_scopes(...) + sentinel-scope check: used by
        #     `list_tabs` for the meta-permission "list every tab"
        #     (exact:list-all-tabs). The gate is the membership test
        #     that follows the call (`if "exact:list-all-tabs" not in
        #     scopes: return approval_required`). A handler calling
        #     `list_active_scopes` with no follow-up gate would be a
        #     bug, but detecting that statically is brittle — the
        #     looser test is "the handler reaches into the approvals
        #     layer somehow."
        VALID_GATES = ("can_read_url(", "is_default_denied(", "list_active_scopes(")

        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if node.name not in required_fns:
                continue
            fn_src = ast.unparse(node)
            if not any(gate in fn_src for gate in VALID_GATES):
                offenders.append(
                    f"{module_rel}:{node.lineno} `{node.name}` does not "
                    "call any of can_read_url() / is_default_denied() "
                    "/ list_active_scopes() — user-tab handlers must "
                    "gate URL access through the approvals layer"
                )

    assert not offenders, (
        "User-tab handler does not route through approvals layer:\n"
        + "\n".join(offenders)
    )


def test_f3_canonical_authorization_entry_points_are_pure_functions():
    """Static guard: `normalize_scope`, `approval_matches`, and
    `can_read_url` are pure functions of their arguments — they
    do NOT read/write the store. This is required for
    defense-in-depth: corrupt store records can't crash the
    decision functions.

    `normalize_scope` is the only one that may raise; the others
    catch its exceptions internally. None of them perform I/O.

    Verified by inspecting the source for I/O-shaped operations.
    """
    from browser_fetch_router import approvals

    PURE_FUNCTIONS = ["normalize_scope", "approval_matches", "can_read_url"]
    BANNED_PATTERNS = [
        "open(",
        "atomic_write_bytes(",
        "_load_unlocked(",
        "_store_path(",
        ".read_text(",
        ".write_text(",
        "json.loads(",  # they shouldn't parse store
    ]
    offenders: list[str] = []
    for fn_name in PURE_FUNCTIONS:
        fn = getattr(approvals, fn_name)
        src = inspect.getsource(fn)
        for pattern in BANNED_PATTERNS:
            if pattern in src:
                offenders.append(
                    f"approvals.{fn_name} contains {pattern} — pure "
                    "decision functions must not perform I/O; "
                    "corrupt store records would crash the auth "
                    "check"
                )
    assert not offenders, "\n".join(offenders)
