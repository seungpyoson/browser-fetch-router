"""Round-14 regression suite — internal adversarial review on
HEAD 20a8859 (post-round-13).

Convention (same as rounds 3-13): each test starts as a REPLICATION that
PASSES on the unfixed code (PASS = bug exists). After the fix lands the
test is INVERTED to assert the now-safe behavior. DISPROVE tests assert
the safe behavior up-front to falsify a claimed finding.

Single class: r14-01

  r14-01 CORRECTNESS: persistent-store readers crash with AttributeError
                      on valid-JSON-of-wrong-shape (e.g., file content
                      `"hacked"`, `[]`, `42`, `null`). `json.loads()`
                      successfully decodes these to non-dict types and
                      every reader's downstream `.get(...)` access
                      explodes. Three convergent sites: cache.py:54,
                      approvals._load_unlocked (247), lifecycle._read_json
                      (99). Same lineage as r6-02 / r9 / i05 silent-dead
                      classes — persistent-store reader assumes a shape
                      the writer never enforced.

Class fix: a single `paths.read_json_dict()` helper that returns a dict
always, treating both parse errors AND wrong-type JSON as corruption
(rename to .json.corrupt-<UTC>, return {}). The three call sites collapse
to thin wrappers (or direct calls) so future persistent stores in this
package automatically get the same safe behavior.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest


# ============================================================
# r14-01 — wrong-shape JSON crashes persistent-store readers
# ============================================================


# Each tuple: (label, file content, what json.loads decodes to)
WRONG_SHAPE_PAYLOADS = [
    ("string", '"hacked"', str),
    ("list", "[1, 2, 3]", list),
    ("number", "42", int),
    ("null", "null", type(None)),
    ("bool", "true", bool),
]


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Point HOME at a tmp_path so config_dir / state_dir / cache_dir all
    land inside an isolated subtree. Every persistent-store path the
    package uses derives from `paths.home()` which reads `$HOME`."""
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


# ----- Site A: approvals store -----


@pytest.mark.parametrize("label,content,_decoded", WRONG_SHAPE_PAYLOADS)
def test_r14_01_approvals_load_handles_wrong_shape_json(
    isolated_home, label, content, _decoded
):
    """Plant a valid-JSON-but-wrong-shape file at the approvals store
    path, then call `list_active_scopes`. Must NOT raise; must return
    an empty list; must rename the corrupt file aside.

    INVERTED: pre-fix, this test would crash with AttributeError on
    `data.get(...)`. Post-fix it asserts (a) no exception, (b) caller
    sees an empty result, (c) a sibling `.corrupt-*` file exists.
    """
    from browser_fetch_router import approvals
    from browser_fetch_router.paths import config_dir, ensure_private_dir

    ensure_private_dir(config_dir())
    store = config_dir() / "approvals.json"
    store.write_text(content)

    # Must not crash (the bug class) — the reader must absorb the
    # corruption and surface the empty state.
    result = approvals.list_active_scopes(session_id="any-session")
    assert result == []

    # Original corrupt file is renamed aside; a sibling matching the
    # `.corrupt-*` suffix exists. Forensics keeps the bytes for audit.
    siblings = list(store.parent.glob("approvals.json.corrupt-*"))
    assert len(siblings) == 1, (
        f"expected one .corrupt-* sibling; got {[p.name for p in store.parent.iterdir()]}"
    )
    # The original path is gone (renamed).
    assert not store.exists()


def test_r14_01_approvals_add_after_corruption_starts_clean(isolated_home):
    """Class-level invariant: after corruption is absorbed, a subsequent
    `add_approval` writes a clean store with the new record. The pre-fix
    `_load_unlocked` only handled parse errors, so wrong-shape JSON
    crashed `add_approval` on `data.setdefault(...)` (and `data["scopes"]`).
    """
    from browser_fetch_router import approvals
    from browser_fetch_router.paths import config_dir, ensure_private_dir

    ensure_private_dir(config_dir())
    store = config_dir() / "approvals.json"
    store.write_text('"hacked"')  # valid JSON, wrong shape

    record = approvals.add_approval(
        "hostname:example.com",
        session_id="s1",
        persisted=True,
    )
    assert record["scope"] == "hostname:example.com"

    # Corrupt sibling is preserved.
    assert any(p.name.startswith("approvals.json.corrupt-") for p in store.parent.iterdir())

    # Store now contains exactly the new record.
    on_disk = json.loads(store.read_text())
    assert isinstance(on_disk, dict)
    assert [s["scope"] for s in on_disk["scopes"]] == ["hostname:example.com"]


# ----- Site B: lifecycle session registry -----


@pytest.mark.parametrize("label,content,_decoded", WRONG_SHAPE_PAYLOADS)
def test_r14_01_lifecycle_read_json_handles_wrong_shape(
    isolated_home, tmp_path, label, content, _decoded
):
    """Plant a valid-JSON-but-wrong-shape file at the session-registry
    path, call `_read_json`. Must return {} and back up the corrupt file.

    INVERTED from a pre-fix crash on `json.loads('"hacked"')` returning
    a str → caller's `data["pids"]` / `data.get("pids")` AttributeErrors.
    """
    from browser_fetch_router import lifecycle

    registry = tmp_path / "registry.json"
    registry.write_text(content)

    result = lifecycle._read_json(registry)
    assert result == {}, f"{label!r} should map to empty dict, got {result!r}"

    # Backup file present.
    siblings = list(tmp_path.glob("registry.json.corrupt-*"))
    assert len(siblings) == 1, f"expected one corrupt-* sibling for {label!r}"
    assert not registry.exists()


# ----- Site C: cache store -----


@pytest.mark.parametrize("label,content,_decoded", WRONG_SHAPE_PAYLOADS)
def test_r14_01_cache_read_handles_wrong_shape_json(
    isolated_home, tmp_path, label, content, _decoded
):
    """Plant a valid-JSON-but-wrong-shape cache entry. `CacheStore.read`
    must return None (cache miss) AND back up the corrupt file so the
    poisoned cache entry doesn't crash every subsequent reader.

    INVERTED from a pre-fix crash on `data.get('schema_version')` against
    a str/list/int — AttributeError exits the CLI with internal_error.
    """
    from browser_fetch_router.cache import CacheStore, cache_key

    store = CacheStore(tmp_path / "cache")
    key = cache_key("test-route", "https://example.com/")
    cache_path = store._path(key)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(content)

    # Must absorb corruption as a cache miss, not crash.
    assert store.read(key) is None

    # Backup file present at the same fan-out directory.
    siblings = list(cache_path.parent.glob(f"{cache_path.name}.corrupt-*"))
    assert len(siblings) == 1, (
        f"expected one corrupt-* sibling for {label!r}; "
        f"got {[p.name for p in cache_path.parent.iterdir()]}"
    )
    assert not cache_path.exists()


def test_r14_01_cache_write_after_corruption_works(isolated_home, tmp_path):
    """After a corrupt cache file is absorbed and backed up, the writer
    can write a fresh entry at the same key and the next read hits it.
    Closes the loop: corruption is recoverable, not terminal.
    """
    from browser_fetch_router.cache import CacheStore, cache_key

    store = CacheStore(tmp_path / "cache")
    key = cache_key("test-route", "https://example.com/")
    cache_path = store._path(key)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text("[]")  # poisoned

    assert store.read(key) is None  # absorbs corruption
    store.write(key, {"envelope_field": "ok"}, ttl_seconds=3600)
    fresh = store.read(key)
    assert fresh == {"envelope_field": "ok"}


# ----- Class-level: helper exists in paths.py and centralizes the policy -----


def test_r14_01_read_json_dict_helper_exists_and_is_used(isolated_home):
    """Lock the class fix in: `paths.read_json_dict` exists, returns a
    dict for any JSON shape, applies backup-and-empty for both parse
    errors and wrong-type JSON. Without this single source of truth a
    future persistent-store reader added to the package would re-introduce
    the bug class.
    """
    from browser_fetch_router.paths import read_json_dict

    # 1. Missing file: empty dict, no error, no sibling created.
    target = isolated_home / "missing.json"
    assert read_json_dict(target) == {}
    assert not list(isolated_home.glob("missing.json.corrupt-*"))

    # 2. Valid dict: returned verbatim.
    target.write_text(json.dumps({"a": 1, "b": [2, 3]}))
    assert read_json_dict(target) == {"a": 1, "b": [2, 3]}

    # 3. Parse error: backup + empty.
    target.write_text("{not-json")
    assert read_json_dict(target) == {}
    assert len(list(isolated_home.glob("missing.json.corrupt-*"))) == 1

    # 4. Wrong-type JSON (the new corruption class): backup + empty.
    target.write_text('"poisoned"')
    assert read_json_dict(target) == {}
    assert len(list(isolated_home.glob("missing.json.corrupt-*"))) == 2


# ============================================================
# r14-disp-g01 — DISPROVE Gemini "Content-Type header injection" claim
# ============================================================


def test_r14_disp_g01_decode_with_charset_no_header_injection_vector():
    """DISPROVE Gemini security-medium on commit 8d3cd4f
    (http_client.py:181): the `cleaned_ctype` translate table omits
    `\\t` (9), `\\n` (10), `\\r` (13). Gemini's claim:

      > can be used for header injection if the Content-Type header is
      > malicious and subsequently used to construct other headers

    The premise is wrong. `_decode_with_charset(body, content_type)`
    consumes `cleaned_ctype` for ONE purpose — charset extraction via
    `email.message.Message.get_content_charset()` — and returns a
    decoded `str`. There is no path from `cleaned_ctype` to header
    construction. The translate table being "incomplete" relative to
    a fully conservative 0-31+127 strip is true on the surface but
    irrelevant: even if CRLF survives the strip, it cannot escape the
    function.

    Per Learning #5 ("DISPROVEN requires reproduction test, not
    prose"): this test exercises the exact attacker scenario for
    each adversarial Content-Type and asserts (a) no exception
    propagates, (b) decode output is normal, (c) the function's
    return type is and remains `str` (locked in via
    `inspect.signature` so a future refactor that returns headers
    or an envelope would trip this test).
    """
    import inspect
    from browser_fetch_router.http_client import _decode_with_charset

    body = b"hello world"
    adversarial = [
        "text/plain\r\nX-Injected: pwned",
        "text/plain\ncharset=utf-8\nX-Injected: pwned",
        "text/plain\rX-Injected: pwned",
        "text/plain;\tcharset=ascii",
        "text/plain\x00; charset=ascii",
        # Defense-in-depth pre-existing cases:
        "",
        None,  # caller may pass None on missing header
    ]
    for ctype in adversarial:
        try:
            result = _decode_with_charset(body, ctype or "")
        except Exception as exc:  # pragma: no cover — should not raise
            pytest.fail(
                f"_decode_with_charset raised {type(exc).__name__}({exc}) "
                f"for adversarial Content-Type {ctype!r}; the function "
                "must absorb every malformed upstream value."
            )
        assert isinstance(result, str), (
            f"_decode_with_charset returned non-str for {ctype!r}: "
            f"{type(result).__name__}"
        )
        # Body is plain ASCII; result must equal it for every charset
        # path the disguised header could try to coerce.
        assert result == "hello world", (
            f"_decode_with_charset corrupted ASCII body for {ctype!r}: "
            f"got {result!r}"
        )

    # Lock in the function contract: return type is `str`. If a future
    # change makes this function return a tuple/dict/headers, or accept
    # cleaned_ctype as an output channel, the static-guard fails — and
    # the surface area Gemini was worried about would actually exist.
    sig = inspect.signature(_decode_with_charset)
    assert sig.return_annotation in (str, "str"), (
        f"_decode_with_charset return type changed to {sig.return_annotation}; "
        "if cleaned_ctype now escapes the function, audit for header "
        "construction paths and tighten the translate table."
    )


def test_r14_01_no_remaining_unsafe_json_load_to_typed_access(isolated_home):
    """Class-level static guard: scan persistent-store readers for the
    pattern `json.loads(path...)` followed shortly by `data.get(` or
    `data["...` — without an `isinstance(data, dict)` gate or a routing
    through `read_json_dict`. New persistent-store readers added later
    that re-introduce the bug class will trip this test.

    The three known sites are explicitly allow-listed below; every other
    site must either (a) not load from a persistent file, or (b) route
    through `read_json_dict`.
    """
    import re

    pkg = Path(__file__).resolve().parents[2] / "browser_fetch_router"
    pattern = re.compile(r"json\.loads\s*\(\s*[^)]*\.read_text")
    typed_access = re.compile(r"data\s*(\.get\s*\(|\[)")

    offenders: list[str] = []
    for py in pkg.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        for match in pattern.finditer(text):
            # Look at the next ~5 lines after the match for a typed
            # access against `data` without an isinstance gate.
            tail = text[match.end(): match.end() + 400]
            if typed_access.search(tail) and "isinstance(data, dict)" not in tail:
                offenders.append(f"{py.relative_to(pkg.parent)}:{text[:match.start()].count(chr(10)) + 1}")

    # After the class fix every persistent-store JSON read goes through
    # `read_json_dict`. The only remaining `json.loads(...read_text...)`
    # patterns should be inside `paths.read_json_dict` itself (which has
    # the isinstance gate) or in tests.
    assert offenders == [], (
        "Found json.loads → typed access without isinstance(data, dict) gate "
        f"(class r14-01): {offenders}. Route through paths.read_json_dict."
    )
