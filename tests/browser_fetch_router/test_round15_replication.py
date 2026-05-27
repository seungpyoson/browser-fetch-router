"""Round-15 regression suite — Greptile P1 security finding on commit 201a050.

Convention (rounds 3-14): each test starts as a REPLICATION that PASSES
on the unfixed code (PASS = bug exists). After the fix lands the test is
INVERTED to assert the now-safe behavior.

Single class: r15-02 (separate from the persistence-contract closing pass
which I labeled r15 internally).

  r15-02 P1-SECURITY: Regional subdomains of host-sensitive entries
                      bypass default-deny. The bare hostnames
                      `console.aws.amazon.com` and
                      `console.cloud.google.com` are listed in
                      `HOST_SENSITIVE_PATTERNS` and `DENY_PATTERNS`
                      without their `*.<host>` wildcard counterparts.
                      `_hostname_matches("console.aws.amazon.com",
                      "us-east-1.console.aws.amazon.com")` is exact
                      equality and returns False, so an agent reading
                      a regional AWS console tab gets full IAM/service
                      output without an approval gate. The
                      module-level invariant comment (lines 10-13)
                      already documents the rule: "every entry that
                      has subdomains in real use also gets a `*.`
                      wildcard." `console.aws.amazon.com` and
                      `console.cloud.google.com` violate the
                      documented invariant.

Class fix: add the missing `*.<host>` and `*.<host>/*` entries AND
add a static-guard test asserting that every bare hostname in the
deny lists has its wildcard counterpart, so the next bare-hostname
addition doesn't silently re-introduce the bypass.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from browser_fetch_router import default_deny


# ============================================================
# r15-02 — regional subdomains bypass default-deny
# ============================================================


REGIONAL_BYPASS_URLS = [
    # AWS regional consoles
    "https://us-east-1.console.aws.amazon.com/iam/home",
    "https://eu-west-1.console.aws.amazon.com/ec2/v2/home",
    "https://ap-northeast-1.console.aws.amazon.com/s3/buckets",
    # GCP project/regional console subdomains (defensive — GCP can
    # introduce them; the bare-hostname-only entry mirrors the same
    # bypass class as AWS regional)
    "https://my-project.console.cloud.google.com/iam-admin",
]


@pytest.mark.parametrize("url", REGIONAL_BYPASS_URLS)
def test_r15_02_regional_subdomain_is_default_denied(url):
    """Each regional/subproject subdomain of a host-sensitive console
    MUST be default-denied. Pre-fix this test fails (returns False)
    because the bare hostname pattern uses exact equality. Post-fix
    the `*.<host>/*` entries match every regional variant.
    """
    assert default_deny.is_default_denied(url) is True, (
        f"{url!r} bypasses default-deny via the regional subdomain "
        "class (Greptile P1-security on 201a050). Class fix: every "
        "bare hostname in DENY_PATTERNS must have its `*.<host>/*` "
        "counterpart."
    )


@pytest.mark.parametrize(
    "host",
    [
        "us-east-1.console.aws.amazon.com",
        "eu-west-1.console.aws.amazon.com",
        "ap-northeast-1.console.aws.amazon.com",
        "my-project.console.cloud.google.com",
    ],
)
def test_r15_02_regional_subdomain_is_host_sensitive(host):
    """Even the BARE hostname (without path) of a regional subdomain
    leaks the fact the user has the console open and MUST be redacted
    in `read-user-tabs list`. Pre-fix the bare regional host slips
    through HOST_SENSITIVE_PATTERNS exact-equality matching.
    """
    assert default_deny.is_default_denied_hostname(host) is True, (
        f"{host!r} is not flagged as host-sensitive — regional console "
        "subdomain bypass class. The wildcard counterpart "
        "`*.<host>` must be added to HOST_SENSITIVE_PATTERNS."
    )


# ============================================================
# Class-level static guard
# ============================================================


def test_r15_02_every_bare_hostname_has_wildcard_counterpart():
    """Class lock-in: every non-wildcard hostname in HOST_SENSITIVE_PATTERNS
    and DENY_PATTERNS must have a corresponding `*.<host>` entry in the
    same list.

    The module-level comment in default_deny.py:10-13 already documents
    this rule:

      > Every entry that has subdomains in real use also gets a `*.`
      > wildcard so `www.gmail.com`, `m.outlook.live.com`, etc. don't
      > bypass the deny via a subdomain the bare entry doesn't match
      > (the matcher does exact equality for non-wildcard entries —
      > this was the round-3 finding U bypass).

    The invariant was documented but not enforced. r15-02 violated it.
    This test enforces it: any future bare-hostname addition without
    its wildcard sibling fails the build instead of silently
    re-introducing the regional-subdomain bypass class.

    The single explicit exception is for hostnames that genuinely have
    no subdomain variants in production AND for which a wildcard
    counterpart would be over-broad. Add such entries to
    `_BARE_HOSTNAME_NO_WILDCARD_ALLOWED` with a one-line justification.
    """
    # Hosts that legitimately don't need a wildcard counterpart. Add
    # entries here ONLY with a written justification — the default
    # answer is "add the wildcard."
    bare_only_allowlist: dict[str, str] = {
        # (none today — the invariant is universal in the current list)
    }

    # Test HOST_SENSITIVE_PATTERNS
    bare_hosts = {
        p for p in default_deny.HOST_SENSITIVE_PATTERNS if not p.startswith("*.")
    }
    wildcards = {
        p[2:] for p in default_deny.HOST_SENSITIVE_PATTERNS if p.startswith("*.")
    }
    missing_in_host_sensitive = sorted(
        bare_hosts - wildcards - set(bare_only_allowlist)
    )
    assert not missing_in_host_sensitive, (
        f"HOST_SENSITIVE_PATTERNS bare hosts without `*.<host>` "
        f"counterpart: {missing_in_host_sensitive}. Class r15-02 "
        "regional-subdomain bypass — every bare hostname with "
        "subdomain variants in production must have its wildcard "
        "sibling. Add entries to bare_only_allowlist with "
        "justification only if the host genuinely has no subdomains."
    )

    # Test DENY_PATTERNS — extract bare hosts (entries that are
    # `<host>/*` not `*.<host>/*`) and check the same invariant.
    deny_bare_hosts: set[str] = set()
    deny_wildcard_hosts: set[str] = set()
    for pattern in default_deny.DENY_PATTERNS:
        host_part = pattern.split("/", 1)[0]
        if host_part.startswith("*."):
            deny_wildcard_hosts.add(host_part[2:])
        elif "/" in pattern:
            # Skip path-sensitive entries from PATH_SENSITIVE_PATTERNS;
            # those intentionally target a single hostname's specific
            # path (e.g., github.com/settings/*) — not a host-deny.
            from browser_fetch_router.default_deny import PATH_SENSITIVE_PATTERNS
            if pattern in PATH_SENSITIVE_PATTERNS:
                continue
            deny_bare_hosts.add(host_part)
    missing_in_deny = sorted(
        deny_bare_hosts - deny_wildcard_hosts - set(bare_only_allowlist)
    )
    assert not missing_in_deny, (
        f"DENY_PATTERNS bare hosts without `*.<host>/*` counterpart: "
        f"{missing_in_deny}. Same regional-subdomain bypass class — "
        "every entry must have both bare and wildcard forms."
    )


# ============================================================
# Round-3 finding U regression — locked in alongside r15-02
# ============================================================


@pytest.mark.parametrize(
    "url",
    [
        "https://www.gmail.com/",
        "https://m.gmail.com/",
        "https://m.outlook.live.com/",
        "https://www.bitwarden.com/",
    ],
)
def test_r3_finding_u_subdomain_bypass_still_blocked(url):
    """Re-verifies the round-3 fix (subdomain bypass via `www.`,
    `m.`, etc.) still holds after the r15-02 class fix lands. The
    same matcher path is shared, so adding new wildcards mustn't
    regress the existing ones.
    """
    assert default_deny.is_default_denied(url) is True, (
        f"{url!r} regressed the round-3 subdomain bypass fix — "
        "existing wildcards must keep matching after r15-02."
    )


# ============================================================
# r15-03 — write-then-chmod permission TOCTOU class
# ============================================================


def test_r15_03_screenshot_output_is_created_at_0o600_not_chmod_after(
    tmp_path, monkeypatch
):
    """`screenshot_tab` previously wrote the PNG via `output.write_bytes`
    (file mode = umask-default, typically 0o644 on most systems) and
    THEN called `os.chmod(output, 0o600)`. Between those two syscalls
    a sibling local-user process could open the file at default
    permissions and read the PNG — which the function's own docstring
    declares "every bit as sensitive as reading its DOM" (a screenshot
    of an inbox is trivially OCR'd).

    Class fix: route the write through `atomic_write_bytes(mode=0o600)`
    which uses tempfile + chmod + os.replace — the mode is set on the
    temp file BEFORE the file is renamed into place, so the visible
    target file is 0o600 from the moment it appears. No TOCTOU window.

    PASS = bug exists (pre-fix): the file mode IS 0o600 after chmod
    runs, but the test mocks chmod to NOT run, simulating the window
    where the chmod hasn't happened yet. The mode then reflects the
    write_bytes default (umask-dependent, but always missing the
    intentional 0o600).

    PASS = safe (post-fix): even with chmod mocked out, the file is
    0o600 because atomic_write_bytes set it via the temp file before
    rename.
    """
    import os
    import stat

    from browser_fetch_router import read_user_tabs

    # Make the auth gate succeed and short-circuit CDP to a fixed PNG
    auth = SimpleNamespace(
        persistent_scopes=("exact:http://test/",),
        exact_one_time_scopes=(),
    )
    monkeypatch.setattr(
        read_user_tabs,
        "_resolve_and_authorize_tab",
        lambda *a, **k: (
            "http://127.0.0.1:9222",
            "http://test/",
            {"id": "t1"},
            auth,
            None,
        ),
    )
    fake_png = b"\x89PNG\r\n\x1a\n" + b"x" * 100
    monkeypatch.setattr(
        "browser_fetch_router.cdp.fetch_tab_screenshot",
        lambda base, tab_id, **_kw: fake_png,
    )
    real_atomic_write = read_user_tabs.atomic_write_bytes
    atomic_calls = []

    def spy_atomic_write(path, data, *, mode=0o600):
        atomic_calls.append((path, data, mode))
        return real_atomic_write(path, data, mode=mode)

    monkeypatch.setattr(read_user_tabs, "atomic_write_bytes", spy_atomic_write)

    # Force a permissive umask so write_bytes would yield 0o644 in the
    # buggy path (so the assertion below has bite on any test runner).
    old_umask = os.umask(0o022)

    # Mock os.chmod to a no-op so we observe ONLY the create-time mode.
    # Pre-fix: write_bytes creates at 0o644, chmod-mock doesn't tighten
    # → final mode is 0o644 → assertion fails (BUG). Post-fix:
    # atomic_write_bytes mkstemp creates at 0o600 + sets mode on temp
    # before rename → final mode is 0o600 even with chmod mocked.
    monkeypatch.setattr(os, "chmod", lambda *a, **k: None)
    try:
        output = tmp_path / "shot.png"
        result = read_user_tabs.screenshot_tab(
            "active",
            output=output,
            session_id="contract-test-sid",
        )
        assert result["status"] == "ok", f"screenshot_tab not ok: {result}"
        assert atomic_calls == [(output, fake_png, 0o600)], (
            "screenshot_tab must route through atomic_write_bytes(mode=0o600), "
            "not only create a final file with matching permissions."
        )
        actual_mode = stat.S_IMODE(output.stat().st_mode)
        assert actual_mode == 0o600, (
            f"screenshot output created at mode {oct(actual_mode)} — "
            "permission TOCTOU class. The chmod-after-write pattern "
            "leaves a window where another local user can read the "
            "sensitive PNG. Route through atomic_write_bytes(mode=0o600) "
            "so the mode is set on the temp file before os.replace."
        )
    finally:
        os.umask(old_umask)


def test_r15_03_install_agent_routes_through_atomic_writer(
    tmp_path, monkeypatch
):
    """`install_agent` previously called `dest.write_text(...)` which
    is non-atomic — a partial write (disk full, signal mid-call,
    permission edge) leaves a half-written SKILL.md on disk. The next
    agent invocation then reads truncated YAML or markdown and fails
    cryptically. Route through `atomic_write_bytes` so partial writes
    are impossible.

    The mode for SKILL.md is intentionally 0o644 (operator-readable;
    not a credential file), so the test asserts the writer routes
    through the helper but does NOT lock in 0o600.
    """
    from browser_fetch_router import install_agent as install_mod
    from browser_fetch_router import paths

    monkeypatch.setenv("HOME", str(tmp_path))
    # Make the verification subprocess a no-op so we don't actually
    # spawn the bfr CLI in tests.
    monkeypatch.setattr(
        install_mod,
        "_run_verification",
        lambda: {"success": True, "failures": []},
    )
    # Pre-create the parent's parent so the missing-parent guard passes.
    (tmp_path / ".claude" / "skills").mkdir(parents=True)

    calls: list = []
    real_atomic = paths.atomic_write_bytes

    def spy(path, data, **kwargs):
        calls.append(path)
        return real_atomic(path, data, **kwargs)

    monkeypatch.setattr("browser_fetch_router.paths.atomic_write_bytes", spy)
    monkeypatch.setattr(
        "browser_fetch_router.install_agent.atomic_write_bytes", spy
    )

    result = install_mod.install_agent("claude")
    assert result["status"] == "ok", f"install_agent not ok: {result}"
    expected_dest = tmp_path / ".claude" / "skills" / "browser-fetch-router" / "SKILL.md"
    assert any(c == expected_dest for c in calls), (
        f"install_agent did not route through atomic_write_bytes; "
        f"calls observed: {calls}. Inline path.write_text is a "
        "partial-write hazard — see r15-03."
    )


def test_r15_03_static_guard_no_path_write_text_or_write_bytes_in_production():
    """Class-level static guard: production code in
    `browser_fetch_router/` MUST NOT use `path.write_text(...)` or
    `path.write_bytes(...)`. Both are non-atomic AND respect umask
    rather than setting a deliberate mode at creation, which is the
    permission-TOCTOU bug class the screenshot path triggered.

    Use `paths.atomic_write_bytes(path, data, mode=0o600|0o644)`
    instead — it uses tempfile + chmod + os.replace, so the visible
    file appears with the intended mode from the moment it exists.

    `paths.py` itself is exempt because that's where the helper lives.
    Tests are exempt because they routinely create fixture files with
    arbitrary permissions.
    """
    import re
    from pathlib import Path

    pkg = Path(__file__).resolve().parents[2] / "browser_fetch_router"
    helpers_module = pkg / "paths.py"

    pattern = re.compile(r"\.write_(text|bytes)\s*\(")
    offenders: list[str] = []

    for py in pkg.rglob("*.py"):
        if py.resolve() == helpers_module.resolve():
            continue
        text = py.read_text(encoding="utf-8")
        for m in pattern.finditer(text):
            line_no = text[: m.start()].count("\n") + 1
            offenders.append(f"{py.relative_to(pkg.parent)}:{line_no}")

    assert not offenders, (
        "Production code uses `path.write_text(...)` or "
        "`path.write_bytes(...)` — non-atomic + umask-default mode. "
        "Class r15-03 (permission TOCTOU + partial-write hazard). "
        "Route through paths.atomic_write_bytes(path, data, mode=...). "
        f"Offenders: {offenders}"
    )
