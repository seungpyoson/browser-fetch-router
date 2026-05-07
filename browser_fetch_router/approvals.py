from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from browser_fetch_router import paths
from browser_fetch_router.default_deny import is_default_denied
from browser_fetch_router.paths import (
    SentinelLock,
    atomic_write_bytes,
    ensure_private_dir,
    read_json_dict,
)

VALID_SCOPE_KINDS = {"exact", "hostname", "wildcard"}

SESSION_TTL_SECONDS = 8 * 60 * 60  # 8 hours of inactivity


def config_dir() -> Path:
    return paths.config_dir()


class InvalidScope(ValueError):
    """The user-supplied approval scope cannot be canonicalized.

    Round-5c added the single-pass `*.` strip but every OTHER malformed
    input (`**.host`, `.host`, `host:port`, empty, etc.) silently fell
    through to `_idna` → `.lower()`, producing a canonical form that
    `approval_matches` never matched (round-6 r6-02). Class fix: validate
    the scope grammar at canonicalization time and raise this error so
    the CLI surfaces a `usage_error` envelope (exit 2) — the user sees
    their typo instead of a silently dead approval.
    """


def _idna(host: str) -> str:
    try:
        return host.encode("idna").decode("ascii").lower()
    except (UnicodeError, UnicodeDecodeError):
        return host.lower()


def _canonicalize_hostname_scope(value: str) -> str:
    """Validate + canonicalize a hostname/wildcard scope value.

    Strips redundant tokens (a single `*.` glob prefix, a trailing FQDN
    dot, surrounding whitespace) so common typo variants collapse to the
    same canonical form. Rejects everything that cannot be a hostname
    (double-asterisks, leading dots, ports, paths, queries, fragments,
    empty strings) so the caller sees a loud `InvalidScope` instead of a
    silently never-matching record.
    """
    v = value.strip()
    # Single-pass `*.` glob-prefix strip (round-5c). The `**.` case is
    # rejected below as `invalid_glob_prefix` rather than allowed
    # through with two strips, since it almost certainly indicates a
    # mistake rather than an intent to match deeper subdomains.
    if v.startswith("*."):
        v = v[2:]
    if not v:
        raise InvalidScope("empty_scope_value")
    if v.startswith("*"):
        raise InvalidScope(f"invalid_glob_prefix:{value!r}")
    # Trailing FQDN dot is harmless on the wire and is the same hostname
    # — collapse it. Lone dots are not.
    v = v.rstrip(".")
    if not v:
        raise InvalidScope(f"scope_value_only_dots:{value!r}")
    if v.startswith("."):
        raise InvalidScope(f"leading_dot_in_scope:{value!r}")
    if ":" in v:
        raise InvalidScope(f"port_in_scope:{value!r}")
    if any(ch in v for ch in ("/", "?", "#", "@", " ", "\t")):
        raise InvalidScope(f"non_hostname_char_in_scope:{value!r}")
    try:
        return v.encode("idna").decode("ascii").lower()
    except (UnicodeError, UnicodeDecodeError) as exc:
        raise InvalidScope(f"invalid_hostname_in_scope:{value!r}") from exc


def normalize_scope(scope: str) -> str:
    """Canonicalize and validate a scope string.

    Single source of truth for scope grammar. Every accepted scope
    has the shape `kind:value` where `kind` is in `VALID_SCOPE_KINDS`
    and `value` passes the kind's canonicalizer. Anything that fails
    one of those legs raises `InvalidScope`.

    The colon kind-separator is required (round-11 i05). Previously
    `normalize_scope("foo")` returned `"foo"` verbatim; `add_approval`
    stored it; `approval_matches` short-circuited on its own ":" check
    and returned False forever — the silent-dead-approval class
    round-6 r6-02 set out to eliminate. Closing the colon-less leg
    here makes "scope passed normalize_scope" a stronger invariant
    that callers (`add_approval`, `revoke_scope`, `approval_matches`)
    can rely on.
    """
    if ":" not in scope:
        raise InvalidScope(f"missing_kind_separator:{scope!r}")
    kind, value = scope.split(":", 1)
    kind = kind.strip().lower()
    if kind in {"hostname", "wildcard"}:
        return f"{kind}:{_canonicalize_hostname_scope(value)}"
    if kind == "exact":
        raw = value.strip()
        # Branch on URL-SHAPE upfront rather than catching UnsafeUrl
        # post-hoc. The earlier round-7 fix routed everything through
        # `normalize_and_validate_url` and caught `UnsafeUrl` as a
        # fallback for sentinels like `exact:list-all-tabs` — but that
        # catch ALSO swallowed SSRF rejections, so a scope like
        # `exact:http://169.254.169.254/` was silently stored as a
        # legitimate exact one-time approval. `can_read_url`'s
        # exact-one-time branch then returned True for the IMDS URL
        # (the transport still blocked the actual fetch as defense in
        # depth, but the broken invariant becomes directly exploitable
        # once CDP text/screenshot extraction is wired in — Greptile
        # P1-security on commit 771f3bc).
        #
        # Class fix: distinguish "this was a URL the user tried to
        # approve" from "this is a non-URL sentinel" by inspecting the
        # parsed scheme. Only URL-shaped inputs go through SSRF
        # validation; UnsafeUrl from that path propagates so
        # `add_approval` rejects the scope with a clear envelope. Bare
        # tokens (no http(s) scheme) are treated as sentinels and
        # preserved verbatim — they never match any URL via
        # `approval_matches` because the URL path canonicalizes
        # through `normalize_and_validate_url`.
        from browser_fetch_router.url_safety import normalize_and_validate_url
        if not raw:
            # Empty `exact:` value would store as a sentinel that
            # matches no URL — same silent-dead-approval class round-6
            # r6-02 fixed for `wildcard:`/`hostname:`. Reject loud
            # (round-11 i03).
            raise InvalidScope("empty_exact_scope_value")
        if urlsplit(raw).scheme in {"http", "https"}:
            canonical = normalize_and_validate_url(raw, allow_loopback=True)
            return f"{kind}:{canonical}"
        return f"{kind}:{raw}"
    # Reject any kind not in VALID_SCOPE_KINDS upfront. The previous
    # fallthrough returned the typo'd kind verbatim (e.g.,
    # `hosname:example.com`), `add_approval` stored the record, and
    # `approval_matches` then silently returned False for every URL
    # because its `kind not in VALID_SCOPE_KINDS` check rejected
    # non-canonical kinds — exactly the silent-dead-approval class
    # round-6 r6-02 was meant to eliminate (Greptile P1 on commit
    # 3b131b7). Surfacing as InvalidScope routes through the CLI
    # dispatcher to a usage_error envelope so the operator sees the
    # rejected kind.
    raise InvalidScope(
        f"unknown_scope_kind:{kind!r} (valid: {sorted(VALID_SCOPE_KINDS)!r})"
    )


def approval_matches(scope: str, url: str) -> bool:
    """Authorization yes/no for a (scope, url) pair.

    Catches InvalidScope (round-9 r9-01 raises on unknown kind) and
    UnsafeUrl (round-7 r7-02 raises on SSRF-blocked exact: URL) as a
    safe `False` so a stale or hand-planted record cannot crash the
    auth check. The write-side gates (`add_approval`,
    `normalize_scope`) refuse to STORE such records; this is
    defense-in-depth for the read side.
    """
    try:
        scope = normalize_scope(scope)
    except (InvalidScope, ValueError):
        return False
    if ":" not in scope:
        return False
    kind, value = scope.split(":", 1)
    if kind not in VALID_SCOPE_KINDS:
        return False
    parsed = urlsplit(url)
    host = _idna(parsed.hostname or "")
    if kind == "exact":
        try:
            return normalize_scope(f"exact:{url}") == f"exact:{value}"
        except (InvalidScope, ValueError):
            return False
    if kind == "hostname":
        return host == value
    if kind == "wildcard":
        return host == value or host.endswith("." + value)
    return False


def can_read_url(
    url: str,
    persistent_scopes: list[str],
    *,
    exact_one_time: list[str] | None = None,
) -> bool:
    """Combined policy: default-deny wins over hostname/wildcard, but an
    exact-URL one-time approval can override default-deny.

    Each scope normalization is wrapped in try/except so a malformed
    record (manually planted in the JSON store, or vintage from before
    a normalization tightening) cannot crash the auth check by raising
    `InvalidScope` / `UnsafeUrl`. Pairs with the `approval_matches`
    defense added in round-9 r9-01 — both write-side and read-side now
    degrade gracefully on stored corruption (round-11 i02).
    """
    exact_one_time = exact_one_time or []
    # Exact one-time approval wins absolutely.
    for scope in exact_one_time:
        try:
            is_exact = normalize_scope(scope).startswith("exact:")
        except (InvalidScope, ValueError):
            continue
        if is_exact and approval_matches(scope, url):
            return True
    if is_default_denied(url):
        return False
    return any(approval_matches(s, url) for s in persistent_scopes)


# --------- Approval store -----------------------------------------------------


def _store_path() -> Path:
    ensure_private_dir(config_dir())
    return config_dir() / "approvals.json"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _expiry_iso(seconds: int = SESSION_TTL_SECONDS) -> str:
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()


def _load_unlocked(path: Path) -> dict[str, Any]:
    """Read the approvals store via the package-wide safe-JSON helper.

    `paths.read_json_dict` is the single source of truth for the
    persistent-store reader pattern in this package. It collapses
    THREE corruption classes (missing file, parse error, wrong-shape
    JSON like `"hacked"` / `[]` / `null`) to one backup-and-empty
    response. The previous local implementation only handled parse
    errors — a planted/truncated file decoding to a string still
    crashed the next typed access in `add_approval` /
    `list_active_scopes` / `revoke_scope` (r14-01). Funneling through
    the helper closes that class for approvals AND any future
    persistent JSON store added to the package gets the same
    forensics-preserving behavior for free.

    Earlier rationale (Gemini high on commit 3b131b7): the previous
    `except (OSError, ValueError): return empty` swallowed corruption
    silently — the next `add_approval` would atomically WIPE every
    prior approval. Backup-and-empty preserves the bytes for forensics
    via a sibling `.corrupt-*` file.
    """
    return read_json_dict(path)


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    atomic_write_bytes(path, json.dumps(payload, sort_keys=True).encode("utf-8"))


# `_StoreLock` was extracted to `paths.SentinelLock` so the same fix that
# closes the lifecycle stale-inode race here also applies to the session
# registry writer (see lifecycle.register_process).


def add_approval(
    scope: str,
    *,
    session_id: str,
    persisted: bool,
    expires_seconds: int = SESSION_TTL_SECONDS,
) -> dict[str, Any]:
    scope = normalize_scope(scope)
    path = _store_path()
    lock_path = config_dir() / ".approvals.lock"
    with SentinelLock(lock_path):
        data = _load_unlocked(path)
        data.setdefault("scopes", [])
        # Replace any prior matching scope.
        data["scopes"] = [s for s in data["scopes"] if s.get("scope") != scope]
        record = {
            "scope": scope,
            "session_id": session_id,
            "persisted": bool(persisted),
            "created_at": _now_iso(),
            "expires_at": _expiry_iso(expires_seconds) if not persisted else None,
        }
        data["scopes"].append(record)
        _atomic_write(path, data)
    return record


def list_active_scopes(*, session_id: str, now: float | None = None) -> list[str]:
    """Return non-expired persistent and session-scoped approvals applicable
    to this session."""
    now_dt = datetime.fromtimestamp(now, UTC) if now else datetime.now(UTC)
    path = _store_path()
    lock_path = config_dir() / ".approvals.lock"
    with SentinelLock(lock_path):
        data = _load_unlocked(path)
    out: list[str] = []
    for s in data.get("scopes", []):
        if s.get("persisted"):
            out.append(s["scope"])
            continue
        expires = s.get("expires_at")
        if not expires:
            continue
        try:
            expires_dt = datetime.fromisoformat(expires)
        except (TypeError, ValueError):
            continue
        if expires_dt > now_dt and s.get("session_id") == session_id:
            out.append(s["scope"])
    return out


def revoke_scope(scope: str) -> dict[str, Any]:
    scope = normalize_scope(scope)
    path = _store_path()
    lock_path = config_dir() / ".approvals.lock"
    with SentinelLock(lock_path):
        data = _load_unlocked(path)
        before = len(data.get("scopes", []))
        data["scopes"] = [s for s in data.get("scopes", []) if s.get("scope") != scope]
        removed = before - len(data["scopes"])
        if removed:
            _atomic_write(path, data)
    return {"removed": removed, "scope": scope}
