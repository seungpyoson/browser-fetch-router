from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from browser_fetch_router.paths import (
    atomic_write_bytes,
    backup_corrupt_file,
    read_json_dict,
)
from browser_fetch_router.schema import SCHEMA_VERSION

# Per-route TTLs in seconds.
ROUTE_TTLS: dict[str, int] = {
    "fxtwitter": 300,
    "reddit-json": 120,
    "jina-reader": 600,
    "parallel": 3600,
}


def cache_key(route: str, normalized_url: str) -> str:
    raw = f"{SCHEMA_VERSION}|{route}|{normalized_url}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _is_valid_cache_record(data: dict[str, Any]) -> bool:
    """Cache-record schema gate (round-17 class G).

    A valid record has:
      - `schema_version`: any value (mismatch is a normal miss, not
        corruption).
      - `expires_at`: numeric (int or float, not bool — bool is a
        Python int subclass but a `True` expires_at is a planted
        garbage value).
      - `envelope`: dict (everything downstream calls `.get(...)` on
        it; a string/list/None envelope is a poison record).

    Returns False for any nested-shape violation so the caller routes
    the file through `backup_corrupt_file` and reports a miss. Top-
    level wrong-shape (`"hacked"`, `[]`, `null`) is caught earlier by
    `read_json_dict`; this function handles the OUTER-DICT-but-
    POISONED-INNER class.
    """
    expires_at = data.get("expires_at")
    if isinstance(expires_at, bool) or not isinstance(expires_at, (int, float)):
        return False
    envelope = data.get("envelope")
    if not isinstance(envelope, dict):
        return False
    return True


class CacheStore:
    """File-backed envelope cache with atomic writes via tempfile + os.replace.

    Each entry stores `created_at`, `expires_at`, `schema_version`, and the
    full envelope. Schema version mismatch causes an automatic miss so a
    schema bump invalidates the entire cache without manual purge.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            self.root.chmod(0o700)
        except OSError:
            pass

    def _path(self, key: str) -> Path:
        # Two-level fan-out keeps directory listings small.
        return self.root / key[:2] / f"{key}.json"

    def read(self, key: str) -> dict[str, Any] | None:
        path = self._path(key)
        # `read_json_dict` returns {} for all three "no usable cached
        # value" cases: missing file (no backup), parse error (corrupt
        # sibling preserved), wrong-shape JSON (corrupt sibling
        # preserved). The previous inline `json.loads(...).get(...)`
        # crashed with AttributeError when a cache file decoded to a
        # non-dict (`"hacked"`, `[]`, `null`, etc.) — class r14-01.
        # Routing through the package-wide helper closes the class
        # here AND makes any future cache poisoning a forensics-friendly
        # miss instead of a CLI internal_error.
        data = read_json_dict(path)
        if not data:
            return None
        # Class-G round-17: validate the FULL record shape, not just
        # the top-level dict. read_json_dict guards top-level wrong-
        # shape JSON; a record like
        #   {"schema_version": V, "expires_at": <num>, "envelope": "str"}
        # passes that gate (top-level IS a dict) but a `str` envelope
        # crashes the caller's `envelope.get(...)` (GPT round-17 P3).
        # Same family of bug for non-numeric `expires_at` ("tomorrow"
        # vs. time.time() raises TypeError on Py3 mixed comparison).
        # The class fix routes the invalid record through corruption-
        # backup (matches persistence-contract invariant F) AND returns
        # None so the caller sees a normal miss.
        if not _is_valid_cache_record(data):
            backup_corrupt_file(path)
            return None
        if data["schema_version"] != SCHEMA_VERSION:
            return None
        if data["expires_at"] < time.time():
            return None
        return data["envelope"]

    def write(self, key: str, payload: dict[str, Any], *, ttl_seconds: int) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.parent.chmod(0o700)
        except OSError:
            pass
        now = time.time()
        record = {
            "schema_version": SCHEMA_VERSION,
            "created_at": now,
            "expires_at": now + max(0, ttl_seconds),
            "envelope": payload,
        }
        atomic_write_bytes(path, json.dumps(record, sort_keys=True).encode("utf-8"))


class InflightLock:
    """Cross-process lock via fcntl.flock on a sentinel file.

    `acquire(timeout_seconds=t)` polls until either the lock is obtained or
    the timeout expires. On timeout the caller should proceed with a
    duplicate provider call rather than fail — better to double-charge once
    than to lock up indefinitely.
    """

    def __init__(self, root: Path, key: str) -> None:
        self.root = Path(root) / "locks"
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            self.root.chmod(0o700)
        except OSError:
            pass
        self.path = self.root / f"{key}.lock"
        self._fd: int | None = None

    def acquire(self, *, timeout_seconds: float = 5.0) -> bool:
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        if self._fd is None:
            self._fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        while True:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except OSError as exc:
                if exc.errno not in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK):
                    raise
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None

    def __enter__(self) -> "InflightLock":
        self.acquire()
        return self

    def __exit__(self, *_exc) -> None:
        self.release()
