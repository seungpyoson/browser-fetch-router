from __future__ import annotations

import fcntl
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def home() -> Path:
    return Path(os.environ.get("HOME", "~")).expanduser()


def config_dir() -> Path:
    return home() / ".config" / "browser-fetch-router"


def state_dir() -> Path:
    return home() / ".local" / "state" / "browser-fetch-router"


def cache_dir() -> Path:
    return home() / ".cache" / "browser-fetch-router"


def ensure_private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


def ensure_all_dirs() -> dict[str, str]:
    return {
        "config": str(ensure_private_dir(config_dir())),
        "state": str(ensure_private_dir(state_dir())),
        "cache": str(ensure_private_dir(cache_dir())),
    }


def write_all(fd: int, data: bytes) -> None:
    """Write `data` to `fd`, looping until every byte is written.

    `os.write(fd, data)` is allowed to return fewer bytes than requested
    (POSIX permits short writes). For O_APPEND files Linux/macOS guarantee
    atomicity only up to PIPE_BUF (~4 KiB); audit lines and cost-mirror
    JSONL records can exceed this. Discarding the return value silently
    truncates JSONL records, leaving malformed JSON that downstream
    consumers either skip or crash on. Loop until `data` is exhausted.
    """
    view = memoryview(data)
    while view:
        n = os.write(fd, view)
        if n <= 0:
            # Defensive: a 0-byte write on a regular fd shouldn't happen,
            # but if it does we'd loop forever. Raise so the caller (which
            # holds an exclusive lock and is mid-append) can release and
            # surface the failure.
            raise OSError(f"write_all: os.write returned {n} on fd {fd}")
        view = view[n:]


class SentinelLock:
    """fcntl.flock around a sentinel file kept SEPARATE from the data file.

    Locks held on a data file that gets atomically replaced (via
    `os.replace`) become locks on the orphaned old inode — useless. Any
    new writer can open the new inode and acquire its lock concurrently,
    racing the original writer and losing entries.

    The fix is to lock a SIBLING file that is NEVER replaced, and use
    that lock to serialize read-modify-write cycles on the data file.
    Used by `approvals.add_approval`, `lifecycle.register_process`, and
    any other shared-state writer that needs cross-process serialization.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fd: int | None = None

    def __enter__(self) -> "SentinelLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX)
        except BaseException:
            os.close(self._fd)
            self._fd = None
            raise
        return self

    def __exit__(self, *_) -> None:
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd = None


def read_json_dict(path: Path) -> dict[str, Any]:
    """Read a JSON file expected to deserialize to an object (dict).

    Single source of truth for the persistent-store reader pattern in
    this package. Returns a dict ALWAYS — callers can safely chain
    `.get(...)` / `["..."]` access without isinstance gates.

    Three corruption classes collapse to the same backup-and-empty
    response:

      1. Missing file              → `{}`, no backup created.
      2. Parse error (truncated /
         non-JSON bytes)            → `{}`, file renamed to
                                       `<name>.corrupt-<UTC-stamp>`.
      3. Wrong-type JSON (valid
         JSON that decodes to a
         string, list, number,
         bool, or null instead of
         an object)                 → `{}`, file renamed as above.

    Class fix for r14-01. Prior implementations of this pattern
    (`cache.read`, `approvals._load_unlocked`, `lifecycle._read_json`)
    each caught `(OSError, ValueError)` from `json.loads` and returned
    an empty dict — but `json.loads('"hacked"')` returns the string
    `"hacked"` with no exception, and every caller's downstream
    `.get(...)` access then crashed with `AttributeError`. Centralizing
    the read AND extending the corruption set to include wrong-type
    JSON closes the class for every persistent JSON store in the
    package, including future ones added by callers who have never
    heard of r14-01.

    The corrupt sibling preserves the original bytes for forensics.
    The user/operator sees a `<name>.corrupt-*` neighbor as the loud
    signal that something went wrong, rather than a silent data wipe
    or an opaque `internal_error` exit. Pairs with `atomic_write_bytes`
    on the writer side: writes are crash-safe, reads are corruption-safe.
    """
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None
    if isinstance(data, dict):
        return data
    # Corruption (parse failure OR wrong-shape JSON) — back up the
    # bytes for forensics, then return empty so the caller continues
    # with a clean state. The rename is best-effort: if it fails (e.g.,
    # cross-device, permission), we still surface empty rather than
    # propagate the OSError up to a CLI internal_error.
    backup_corrupt_file(path)
    return {}


def backup_corrupt_file(path: Path) -> None:
    """Rename a corrupt persistent file to a forensics-friendly sibling.

    Single source of truth for the corruption-backup pattern. Called
    from `read_json_dict` for top-level wrong-shape JSON, AND from
    `cache.CacheStore.read` for nested-shape corruption (envelope is
    not a dict, expires_at is non-numeric, etc.) so the same forensic
    trail exists regardless of which validation layer caught the
    corruption.

    Microsecond stamp + uniqueness suffix. The previous second-
    resolution stamp collided when two corruption events landed in the
    same wall-clock second — Path.rename silently overwrites the
    existing target on POSIX, which would WIPE the earlier forensic
    backup (the very thing this branch exists to preserve). The `-N`
    counter is a defensive last-mile guard against any remaining
    collision (clock skew, batched corruption from a restored
    snapshot, etc.).

    Best-effort: failure (cross-device, permission) is swallowed so
    callers always get a clean state instead of an OSError that
    propagates up to a CLI internal_error.
    """
    if not path.exists():
        return
    try:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S_%fZ")
        target = path.with_suffix(path.suffix + f".corrupt-{stamp}")
        counter = 0
        while target.exists():
            counter += 1
            target = path.with_suffix(
                path.suffix + f".corrupt-{stamp}-{counter}"
            )
        path.rename(target)
    except OSError:
        pass


def append_durable_line(path: Path, line: bytes, *, mode: int = 0o600) -> None:
    """Atomically append `line` to `path` with crash-safe durability.

    Single source of truth for the append-only JSONL writer pattern in
    this package (audit log, cost mirror, future forensic streams).

    Invariants enforced (matches `docs/browser-fetch-router-persistence-contract.md`):

      A. Atomic line append — fcntl.flock(LOCK_EX) serializes concurrent
         writers; `write_all` loops until every byte hits the kernel,
         so a line longer than PIPE_BUF (~4 KiB) is never split across
         processes.

      C. Durability — `os.fsync(fd)` after the write forces the bytes
         to durable storage before close. Without this, a power loss
         or kernel panic between the write and the next pdflush cycle
         (typically up to 30 seconds on Linux/macOS) silently drops
         the appended line. Forensic logs MUST survive crashes — an
         attacker who can trigger SSRF blocks AND cause a crash could
         otherwise erase evidence of the attack attempt (r15-01).

      D. Concurrency safety — flock on the file fd serializes all
         writers system-wide. The data file isn't atomically replaced
         (it's append-only), so the inode is stable and the lock
         doesn't suffer the stale-inode race that motivated
         `SentinelLock` for object stores.

      E. Permission isolation — `0o600` on creation. For stores added
         to existing files with different modes, callers should
         `os.chmod` the path explicitly before the first call (or
         delete + recreate); this helper does NOT downgrade an
         already-permissive existing file because that could break
         operator-managed permissions intentionally widened (e.g.,
         a logging-group setup).

    The line bytes MUST end with `\\n`. Caller's responsibility to
    encode and terminate; the helper does not append separators.

    Class fix for r15-01. Replaces inlined `os.open + flock + write_all
    + close` blocks at audit.append_audit and cost.CostLedger._mirror —
    both previously omitted fsync. Future append-log writers route
    through this helper; the contract test
    `test_no_adhoc_persistent_writes` fails the build for any production
    `os.open(O_APPEND)` outside `paths.py`.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, mode)
    locked = False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        locked = True
        write_all(fd, line)
        # Durability: bytes must be on disk before we drop the lock.
        # `os.fsync` blocks until the device's write cache acknowledges
        # the data is persistent. The cost is one syscall per append —
        # acceptable for forensic logs which run at human-CLI cadence
        # (one event per `bfr read-web` invocation, etc.).
        os.fsync(fd)
    finally:
        if locked:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                # If unlock fails the close below still drops the fd,
                # which releases the flock at the kernel layer. Don't
                # swallow the original error if write_all/fsync raised.
                pass
        try:
            os.close(fd)
        except OSError:
            pass


def atomic_write_bytes(path: Path, data: bytes, *, mode: int = 0o600) -> None:
    """Atomically write `data` to `path`.

    Single source of truth for atomic-rename writes across the package.
    Cache, approval store, and session-registry writers all funnel through
    this helper instead of repeating tempfile + os.replace blocks.

    Crash-safety: if anything between mkstemp and os.replace fails (write,
    fsync, chmod), the temp file is unlinked before the exception is
    re-raised. Without this, a half-finished write leaves a stale
    `.<name>.<random>.tmp` sibling in the target directory that grows
    unbounded across crashes.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    tmp_path = Path(tmp_name)
    success = False
    fd_owned_by_file = False
    try:
        # `os.fdopen(fd, ...)` takes ownership of `fd` only on success.
        # If it raises (EMFILE / MemoryError under load), the raw `tmp_fd`
        # stays open and would leak indefinitely without explicit cleanup.
        # `fd_owned_by_file = True` flips ONLY after fdopen succeeds; the
        # finally block then closes `tmp_fd` if and only if ownership
        # never transferred.
        fh = os.fdopen(tmp_fd, "wb")
        fd_owned_by_file = True
        with fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.chmod(tmp_path, mode)
        except OSError:
            pass
        os.replace(tmp_path, path)
        success = True
    finally:
        if not fd_owned_by_file:
            try:
                os.close(tmp_fd)
            except OSError:
                pass
        if not success:
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass


# ---------------------------------------------------------------------------
# Class-D round-17: agent-channel filesystem write containment
# ---------------------------------------------------------------------------
#
# The CLI is the gate every agent (Claude, Codex, Gemini, …) goes through
# when it requests a filesystem write — `install-agent --adapter-path`
# and `read-user-tabs screenshot --output`. Same-user filesystem
# permissions don't bound the threat: the agent runs AS the user, so it
# can already write anywhere the user can. The CLI's job is to refuse
# to BE the writing tool for paths that don't match the operation's
# intent.
#
# Two helpers, one per operation. Both validate by FILE SHAPE (basename,
# extension), NOT by system-path denylist. Symlinks on the path are
# intentionally NOT rejected: `os.replace` (the destination of
# `atomic_write_bytes`) does not follow symlinks for the rename target —
# a symlinked target gets REPLACED with a regular file, leaving the
# original symlink-target intact. Verified at the round-17-followup
# adversarial review (commit f9473a7 → followup):
#
#   atomic_write_bytes(symlink_to_bashrc, b"...")
#   → symlink replaced with regular file; bashrc content untouched.
#
# So symlink-rejection is unnecessary defense-in-depth. The original
# round-17 implementation included `_reject_symlink_on_path` and broke
# the documented `/tmp/screenshot.png` use case on macOS (where /tmp is
# a system symlink to /private/tmp). Removing the over-aggressive check
# closes the F-17a regression.


class UnsafeDestination(ValueError):
    """A caller-supplied write destination failed Class-D containment.

    Surfaced to the CLI dispatcher as `tool_setup_failed` so the user
    sees the rejected path with a clear reason. Distinct from
    `InvalidScope` / `InvalidSessionId` because this is an
    agent-channel write boundary, not a grammar issue."""


def validate_skill_md_dest(adapter_path: str | Path) -> Path:
    """Validate `--adapter-path` for `install-agent`.

    Class-D round-17. install-agent only ever writes a `SKILL.md`
    file; the basename is the entire security boundary. A path like
    `~/.bashrc` has the wrong basename and is rejected without
    consulting any system-path denylist (denylists are
    permanently-incomplete; shape-based checks are not).

    Symlinks on the path are NOT rejected: `os.replace` (used by
    `atomic_write_bytes`) does not follow symlinks for the
    destination — a symlinked SKILL.md target gets replaced with a
    regular file containing the new SKILL.md bytes, and the
    original symlink-target file is untouched. The basename check is
    the security boundary, not the symlink-on-path walk.

    Returns the validated Path (after `expanduser`); raises
    `UnsafeDestination` on basename mismatch.
    """
    p = Path(adapter_path).expanduser()
    if p.name != "SKILL.md":
        raise UnsafeDestination(
            f"--adapter-path must point to a file named SKILL.md, "
            f"got {p.name!r}. install-agent only ever writes SKILL.md; "
            f"refusing to overwrite an unrelated file."
        )
    return p


def validate_image_dest(output: str | Path) -> Path:
    """Validate `--output` for `read-user-tabs screenshot`.

    Class-D round-17. Two basename-shape checks:

      - Extension MUST be `.png`, `.jpg`, or `.jpeg`. Screenshot
        writes PNG bytes; other extensions (`authorized_keys`,
        `passwd`, no extension) are rejected without a system-path
        denylist.
      - Basename MUST NOT start with `.`. Hidden files are user
        config / dotfiles by convention — refusing to overwrite them
        prevents agent-channel corruption of `.bash_history`,
        `.npmrc`, etc. Hidden DIRECTORIES on the path are allowed
        (legitimate `~/Pictures/.archive/today.png`).

    Symlinks on the path are NOT rejected (see
    `validate_skill_md_dest` rationale). This permits `/tmp/foo.png`
    on macOS where `/tmp` is a system symlink to `/private/tmp`,
    which the operator must be able to use as scratch space.

    Returns the validated Path (after `expanduser`); raises
    `UnsafeDestination` on dotfile basename or wrong extension.
    """
    p = Path(output).expanduser()
    if p.name.startswith("."):
        raise UnsafeDestination(
            f"--output basename {p.name!r} starts with '.' — refusing "
            "to overwrite a hidden file (dotfile / user config)."
        )
    if p.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
        raise UnsafeDestination(
            f"--output must end in .png, .jpg, or .jpeg, got "
            f"{p.suffix!r}. Screenshot writes PNG bytes; refusing to "
            f"write to a path with the wrong extension."
        )
    return p
