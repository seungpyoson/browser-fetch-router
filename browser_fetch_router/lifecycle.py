from __future__ import annotations

import errno
import json
import os
import re
import signal
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from browser_fetch_router.paths import (
    SentinelLock,
    atomic_write_bytes,
    ensure_private_dir,
    read_json_dict,
    state_dir,
)
from browser_fetch_router.schema import envelope


# Session IDs reach this module from BFR_SESSION_ID, --session-id flags,
# and BFR_INVOKING_AGENT — all caller-controllable. Without a grammar
# constraint a value like "../audit" composes into
# `sessions/../audit.json` and `path.unlink()` deletes the unrelated
# `audit.jsonl` registry sibling (round-6 r6-05). Validate at the single
# place that maps an ID to a Path so every caller — register_process,
# run_cleanup, future helpers — gets the same containment guarantee.
_SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class InvalidSessionId(ValueError):
    """A caller-supplied session identifier failed the grammar check.

    Surfaced to the CLI dispatcher as a `usage_error` envelope so the
    user sees the rejected ID instead of a silent skip or an unrelated
    file deletion."""


def validate_session_id(session_id: str) -> str:
    """Reject anything that cannot be a session ID before it becomes a
    path component. Allows alphanumerics, dash, and underscore (the
    UUID/ULID/slug-friendly subset); rejects empty, overlong, dot,
    slash, NUL, and every other path-traversal vector. Single source of
    truth for session-ID grammar."""
    if not isinstance(session_id, str) or not _SESSION_ID_PATTERN.fullmatch(session_id):
        raise InvalidSessionId(f"invalid_session_id:{session_id!r}")
    return session_id

# Single source of truth for the session-registry schema. The reaper, the
# writer, and any external integration (e.g. global-orphan-reap, doctor)
# must speak these exact field names. They mirror the spec at
# docs/superpowers/specs/2026-05-06-browser-fetch-router-v1-implementation-spec.md
# (Session registry section).
REGISTRY_PROCESSES_KEY = "local_processes"
REGISTRY_PROCESS_GROUP_KEY = "process_group"
REGISTRY_OUTCOME_KEYS = ("cleaned", "skipped", "failed")


def session_registry_dir() -> Path:
    return ensure_private_dir(state_dir() / "sessions")


def session_registry_path(session_id: str) -> Path:
    """Map a session ID to its registry JSON path.

    Validates the ID against the grammar AND verifies the resolved path
    sits inside the sessions registry directory. The dual check is
    defense-in-depth: if a future maintainer routes around
    `validate_session_id`, the containment assertion still catches the
    traversal. Round-6 r6-05.
    """
    sid = validate_session_id(session_id)
    sessions_dir = session_registry_dir().resolve()
    candidate = (sessions_dir / f"{sid}.json").resolve()
    if not str(candidate).startswith(str(sessions_dir) + os.sep):
        raise InvalidSessionId(f"session_id_escapes_registry:{session_id!r}")
    return candidate


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    atomic_write_bytes(path, json.dumps(payload, sort_keys=True).encode("utf-8"))


def _read_json(path: Path) -> dict[str, Any]:
    """Read the session-registry JSON via the package-wide safe-JSON helper.

    `paths.read_json_dict` is the single source of truth for the
    persistent-store reader pattern in this package. It collapses
    THREE corruption classes (missing file, parse error, wrong-shape
    JSON like `"hacked"` / `[]` / `null`) to one backup-and-empty
    response. The previous local implementation only handled parse
    errors — a planted/truncated file decoding to a string still
    crashed the next typed access in `register_process` /
    `run_cleanup` (r14-01). Funneling through the helper closes that
    class for the session registry AND keeps the existing data-loss
    defense (backup preserved as a `.json.corrupt-*` sibling) intact.

    Earlier rationale (Gemini medium on 3b131b7): returning empty
    on corruption silently lets `register_process` overwrite the
    file with a single-process store, wiping any prior process
    records. Backup-and-empty preserves the bytes for forensics.
    """
    return read_json_dict(path)


def _read_fd_to_eof(fd: int, *, chunk_size: int = 65536) -> bytes:
    """Read from `fd` until EOF.

    `os.read(fd, n)` is allowed to return short — POSIX permits any value
    from 1 to n. Reading once with a 1 MB cap silently truncates oversized
    files AND can return less than the requested amount even on small ones.
    Loop until EOF (read returns b"") for correctness.
    """
    chunks: list[bytes] = []
    while True:
        chunk = os.read(fd, chunk_size)
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks)


def _registry_lock_path(session_id: str) -> Path:
    """Sibling sentinel-lock file used to serialize register_process /
    run_cleanup writes for a session. Kept SEPARATE from the data file
    because the data file gets atomically replaced (os.replace), which
    creates a new inode — any flock held on the old inode is useless,
    letting concurrent writers race and lose entries (the round-3
    `test_D_lifecycle_flock_stale_inode_race` repro)."""
    return session_registry_dir() / f".{session_id}.lock"


def register_process(
    session_id: str,
    *,
    pid: int,
    create_time: float,
    process_group: int | str | None = None,
    info: dict[str, Any] | None = None,
) -> None:
    """Append a launched process to the session registry.

    Field names follow the v1 implementation spec (Session registry section):
    `local_processes` and `process_group`. The reaper in `run_cleanup` reads
    these same names. Renaming either side is a contract change that needs
    both sides updated together — the schema constants at module top exist
    so future renames break loudly.

    Locking uses a sibling sentinel file (`SentinelLock`) so the lock
    survives the atomic rename of the data file. flock on the data file
    itself becomes useless the moment `atomic_write_bytes` replaces the
    inode — concurrent registrations could then both acquire the lock on
    different inodes and overwrite each other.
    """
    path = session_registry_path(session_id)
    with SentinelLock(_registry_lock_path(session_id)):
        data = _read_json(path)
        data.setdefault("session_id", session_id)
        data.setdefault(REGISTRY_PROCESSES_KEY, [])
        data[REGISTRY_PROCESSES_KEY].append({
            "pid": int(pid),
            "create_time": float(create_time),
            REGISTRY_PROCESS_GROUP_KEY: process_group,
            "registered_at": time.time(),
            "info": info or {},
        })
        data.setdefault("started_at", datetime.now(UTC).isoformat())
        data["cleanup_status"] = "pending"
        _atomic_write_json(path, data)


def should_kill_process(
    *,
    expected_pid: int,
    expected_create_time: float,
    observed_pid: int,
    observed_create_time: float,
) -> bool:
    """Cleanup must verify both PID and create_time before signaling.

    Linux/macOS reuse PIDs aggressively; matching only on PID can kill an
    unrelated process the OS happened to assign that ID later.
    """
    return (
        expected_pid == observed_pid
        and abs(expected_create_time - observed_create_time) < 1e-3
    )


def _kill_pid_safely(pid: int, expected_create_time: float, *, dry_run: bool) -> str:
    """Best-effort kill that verifies PID + create_time via psutil and
    terminates the whole process tree (leader + descendants), with
    SIGKILL escalation on SIGTERM-ignoring survivors.

    Returns one of: 'cleaned', 'skipped', 'failed'.

    - 'cleaned' = process is verifiably GONE after the kill sequence.
    - 'skipped' = PID/create_time mismatch (record is stale; not ours).
    - 'failed' = something is still alive after SIGKILL (D-state,
       permission, kernel issue) OR psutil itself failed.

    Class fix for round-11 i06: the previous implementation declared
    "cleaned" after the SIGTERM grace period regardless of whether the
    process actually exited, and `run_cleanup` then unlinked the
    registry file unconditionally. A SIGTERM-ignoring browser process
    therefore survived AND became untracked. Now:

    1. SIGTERM children + leader (children first so descendants don't
       reparent to PID 1 mid-walk).
    2. wait_procs(timeout=1.0) — graceful exit window.
    3. SIGKILL any survivors.
    4. wait_procs(timeout=0.5) — let SIGKILL land.
    5. Anything still alive → "failed". `run_cleanup` then preserves
       the registry file so future `--global-orphan-reap` retries.

    Round-6 r6-03 closed the "only-signal-leader" gap; round-11 i06
    closes the "lie-about-cleanup-success" gap. Together: every
    "cleaned" outcome means the tree is gone and the registry can
    safely be unlinked.
    """
    try:
        import psutil  # type: ignore
    except ImportError:
        return "skipped"
    try:
        proc = psutil.Process(pid)
        observed_create = proc.create_time()
    except psutil.NoSuchProcess:
        return "cleaned"  # Already gone.
    except psutil.Error:
        return "failed"
    if not should_kill_process(
        expected_pid=pid,
        expected_create_time=expected_create_time,
        observed_pid=pid,
        observed_create_time=observed_create,
    ):
        return "skipped"
    if dry_run:
        return "cleaned"
    # Enumerate the descendant tree BEFORE we signal the leader. Once
    # the leader is gone, `proc.children(recursive=True)` returns an
    # empty list (or raises NoSuchProcess) and the orphans are missed.
    try:
        descendants = proc.children(recursive=True)
    except psutil.Error:
        descendants = []
    # Phase 1: SIGTERM children first.
    for child in descendants:
        try:
            child.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    # Then SIGTERM the leader.
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        # Leader already gone between the children() walk and now.
        # Fall through to wait + escalation on the children we already
        # signaled.
        pass
    except PermissionError:
        return "failed"
    except OSError as exc:
        if exc.errno not in (errno.ESRCH,):
            return "failed"
    # Phase 2: graceful exit window.
    try:
        gone, alive = psutil.wait_procs(
            [proc, *descendants], timeout=1.0
        )
    except psutil.Error:
        gone, alive = [], [proc, *descendants]
    if not alive:
        return "cleaned"
    # Phase 3: SIGKILL escalation on SIGTERM-ignoring survivors.
    for survivor in alive:
        try:
            survivor.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    # Phase 4: brief wait for SIGKILL to land.
    try:
        gone2, still_alive = psutil.wait_procs(alive, timeout=0.5)
    except psutil.Error:
        still_alive = alive
    if still_alive:
        # SIGKILL ignored — process likely in uninterruptible (D) state
        # or kernel cannot deliver the signal. Surface as "failed" so
        # `run_cleanup` keeps the registry record for a future retry.
        return "failed"
    return "cleaned"


def run_cleanup(
    *,
    all_sessions: bool = False,
    global_orphan_reap: bool = False,
    logs: bool = False,
    max_age_days: int = 30,
    session_id: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Clean session registry entries.

    Two cleanup scopes are intentionally separate flags. The CLI maps
    `--all` to `all_sessions=True` (current-session targeting) and
    `--global-orphan-reap` to `global_orphan_reap=True` (cross-session
    age-based reap). Both can be passed together.

    - `all_sessions=True` (with `session_id`): clean every process
      registered under the GIVEN session_id. The `--all` CLI flag
      surfaces this — "all processes for this session", NOT "all
      sessions". A `session_id` is required; without it, this scope
      no-ops. Pairs with `BFR_SESSION_ID` env or `--session-id`.
    - `global_orphan_reap=True`: cross-session reaper; clean every
      registry file whose mtime is older than `max_age_days`,
      regardless of which session it belongs to. Use after a crash or
      across CI boxes where the original session-id is unknown.
    - `logs=True`: rotate `audit.jsonl` / `cost.jsonl` mirror logs
      older than `max_age_days` (or >100 MB).
    - `dry_run=True`: side-effect-free preview. SIGTERM is skipped
      and the registry file is preserved (round-6 r6-05).

    Default `all_sessions=False` and `global_orphan_reap=False` means
    the function returns an empty result envelope — there is no
    "default scope". The phrasing in the prior docstring was
    misleading on this point (Gemini medium on commit f2a99d0).
    """
    registry_dir = session_registry_dir()
    results: list[dict[str, Any]] = []
    now = time.time()
    cutoff = now - max_age_days * 86400

    targets: list[Path] = []
    if all_sessions and session_id:
        target = session_registry_path(session_id)
        if target.exists():
            targets.append(target)
    if global_orphan_reap:
        for entry in registry_dir.glob("*.json"):
            try:
                stat = entry.stat()
            except OSError:
                continue
            if stat.st_mtime < cutoff:
                targets.append(entry)

    for path in dict.fromkeys(targets):  # preserve order, dedupe
        data = _read_json(path)
        sid = data.get("session_id", path.stem)
        # Initialize buckets matching the values _kill_pid_safely actually
        # returns — the previous "killed" key was never populated because
        # _kill_pid_safely emits "cleaned" on success, leaving the killed
        # list always empty in the result envelope.
        per_session: dict[str, Any] = {"session_id": sid}
        for outcome_key in REGISTRY_OUTCOME_KEYS:
            per_session[outcome_key] = []
        per_session["malformed"] = []
        # `_read_json` (paths.read_json_dict) guarantees `data` is a
        # dict, but the value at REGISTRY_PROCESSES_KEY can still be
        # any JSON shape — a list of records is the contract, but a
        # corrupt or externally-mutated registry could yield a string
        # / number / dict / null. Without the explicit type-check
        # below, a string at this key would be iterated character-by-
        # character and `entry.get("pid", 0)` would raise AttributeError
        # on the first char, crashing the cleanup CLI.
        processes = data.get(REGISTRY_PROCESSES_KEY, [])
        if not isinstance(processes, list):
            processes = []
        for entry in processes:
            # Per-entry corruption defense. Three forms collapse to
            # the same "malformed" bucket: (a) entry is not a dict,
            # (b) `pid` is non-coercible to int, (c) `create_time` is
            # non-coercible to float. Skip with logging rather than
            # crash so a single bad record cannot prevent cleanup of
            # valid sibling processes in the same registry. The
            # malformed bucket is reported in the envelope so the
            # operator sees the corruption signal; entries with
            # invalid pid CAN'T be acted on (we don't know what
            # process they refer to), so they don't block the unlink
            # gate below.
            if not isinstance(entry, dict):
                per_session["malformed"].append({"raw": entry})
                continue
            try:
                pid = int(entry.get("pid", 0))
                create_time = float(entry.get("create_time", 0.0))
            except (TypeError, ValueError):
                per_session["malformed"].append({"raw": entry})
                continue
            outcome = _kill_pid_safely(pid, create_time, dry_run=dry_run)
            # `_kill_pid_safely` returns one of REGISTRY_OUTCOME_KEYS by
            # contract — direct subscript so any future contract drift
            # raises a loud KeyError here instead of being silently
            # miscategorized as `failed` (Gemini medium on 9a26fb2:
            # fail-loud > defensive-fallback for schema invariants).
            per_session[outcome].append({"pid": pid})
        # Unlink the registry only when:
        #   - dry_run=False (round-6 r6-05: dry_run is side-effect-free), AND
        #   - no entry remains "failed" (round-11 i06: a SIGKILL-ignoring
        #     survivor must stay tracked so a future
        #     `cleanup --global-orphan-reap` can retry it; otherwise the
        #     surviving process becomes invisible orphan).
        if not dry_run and not per_session["failed"]:
            try:
                path.unlink()
            except OSError:
                pass
        results.append(per_session)

    log_results: dict[str, Any] = {}
    if logs:
        log_results = _rotate_logs(
            max_age_days=max_age_days, now=now, dry_run=dry_run
        )

    return envelope(
        command="cleanup",
        status="ok",
        evidence={
            "session_scope": session_id if all_sessions else None,
            "global_orphan_reap": global_orphan_reap,
            "results": results,
            "log_rotation": log_results,
            "max_age_days": max_age_days,
        },
    )


def _rotate_logs(
    *, max_age_days: int, now: float, dry_run: bool = False
) -> dict[str, Any]:
    """Rotate `audit.jsonl` / `cost.jsonl` when oversized or aged-out.

    `dry_run=True` lists the files that WOULD rotate without renaming —
    same dry-run contract `run_cleanup` enforces for the registry path
    (round-6 r6-05). Without the dry-run gate here, a caller asking
    for a side-effect-free preview would silently move audit + cost
    journals aside (round-11 i01).
    """
    cutoff = now - max_age_days * 86400
    sd = state_dir()
    rotated: list[str] = []
    for name in ("audit.jsonl", "cost.jsonl"):
        path = sd / name
        if not path.exists():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        # Per-spec: 100MB or daily rotation. v1 uses size-based check + age trim.
        if stat.st_size > 100 * 1024 * 1024 or stat.st_mtime < cutoff:
            archived = sd / f"{name}.{int(now)}.archive"
            if dry_run:
                # Report the would-be archive name so the caller sees
                # the planned rotation; nothing on disk changes.
                rotated.append(str(archived))
                continue
            try:
                path.rename(archived)
                rotated.append(str(archived))
            except OSError:
                continue
    return {"rotated": rotated, "dry_run": dry_run}
