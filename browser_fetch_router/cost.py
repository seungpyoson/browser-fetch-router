from __future__ import annotations

import json
import math
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from browser_fetch_router.paths import append_durable_line, ensure_private_dir

# Provider-level token bucket defaults (capacity per refill window).
# Buckets are PROCESS-SHARED — every CLI invocation pulls from the same
# table — but circuit breakers are SESSION-SCOPED.
PROVIDER_BUCKETS: dict[str, dict[str, float]] = {
    "fxtwitter": {"capacity": 30, "refill_seconds": 60},
    "reddit-json": {"capacity": 1, "refill_seconds": 2},
    "jina-reader": {"capacity": 20, "refill_seconds": 60},
    "parallel": {"capacity": 10, "refill_seconds": 60},
}

CIRCUIT_BREAKER_THRESHOLD = 3
CIRCUIT_BREAKER_OPEN_SECONDS = 300


@contextmanager
def _connect(path: Path) -> Iterator[sqlite3.Connection]:
    """Yield a sqlite3 connection that is GUARANTEED to close on context exit.

    Using bare `with sqlite3.connect(...) as conn:` is a common bug — Python's
    sqlite3 `__exit__` only commits/rollbacks the transaction; it does NOT
    close the connection. Across many invocations that leaks file descriptors
    until garbage collection. This contextmanager closes explicitly.

    Callers manage their own transactions via explicit `BEGIN IMMEDIATE` /
    `COMMIT` SQL since `isolation_level=None` is set.
    """
    conn = sqlite3.connect(path, timeout=10.0, isolation_level=None)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA synchronous=NORMAL")
        yield conn
    finally:
        conn.close()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS costs (
            audit_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            day TEXT NOT NULL,
            provider TEXT NOT NULL,
            estimated_cost_usd REAL NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS costs_session ON costs(session_id);
        CREATE INDEX IF NOT EXISTS costs_day ON costs(day);

        CREATE TABLE IF NOT EXISTS token_buckets (
            provider TEXT PRIMARY KEY,
            tokens REAL NOT NULL,
            updated_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS circuit_breakers (
            session_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            consecutive_failures INTEGER NOT NULL,
            opened_until REAL,
            PRIMARY KEY (session_id, provider)
        );

        CREATE TABLE IF NOT EXISTS paid_disabled_sessions (
            session_id TEXT PRIMARY KEY,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )


class CostLedger:
    """SQLite-backed ledger with reservation rollback, token buckets, circuit
    breakers, and paid-session lockouts. WAL mode + BEGIN IMMEDIATE makes the
    cap-checking + insert atomic across processes."""

    def __init__(self, path: Path, *, mirror_path: Path | None = None) -> None:
        self.path = Path(path)
        # Class-H: route the parent dir through `ensure_private_dir` so
        # cost.db lives under a 0o700 directory matching every other
        # persistent store. Plain `mkdir(parents=True, exist_ok=True)`
        # used to leave the parent at the umask default (0o755), letting
        # other local users `cd` into ~/.local/state/browser-fetch-
        # router/ even if cost.db itself were 0o600.
        ensure_private_dir(self.path.parent)
        self.mirror_path = Path(mirror_path) if mirror_path else self.path.parent / "cost.jsonl"
        with _connect(self.path) as conn:
            _ensure_schema(conn)
        # Class-H: SQLite creates the database file at the umask default
        # (typically 0o644), exposing cost history to any local user.
        # Tighten to 0o600 to match the persistence-contract invariant E
        # for every JSON store. Done AFTER `_ensure_schema` so the file
        # exists; chmod is idempotent across the WAL/SHM siblings.
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            # Inherit the persistence-contract behavior on hostile
            # filesystems (read-only mounts, exotic filesystems): the
            # mode set is best-effort and does not block init.
            pass

    # --------- Reservations -------------------------------------------------

    def reserve(
        self,
        session_id: str,
        provider: str,
        amount: float,
        *,
        request_cap: float,
        session_cap: float,
        daily_cap: float,
    ) -> str | bool:
        """Atomically check caps + insert a reservation. Returns the audit_id
        on success or False if any cap would be exceeded. Caller MUST call
        release(audit_id) on provider failure."""
        # Class-E: guard non-finite caps as operator config bugs and
        # non-finite amounts as caller-supplied data corruption. NaN
        # comparisons (`nan < 0`, `nan > x`) all return False — without
        # this guard a NaN amount silently bypassed both `< 0` and
        # `> request_cap`, then propagated into SQLite where SUM treats
        # NaN as 0 in some contexts. Operator-config bugs (NaN/Inf cap)
        # raise ValueError so the misconfig surfaces immediately;
        # caller-data bugs (NaN/Inf amount) return False quietly so the
        # CLI emits a normal `cost_cap_exceeded` envelope rather than
        # crashing the agent.
        for label, value in (
            ("request_cap", request_cap),
            ("session_cap", session_cap),
            ("daily_cap", daily_cap),
        ):
            if not math.isfinite(value):
                raise ValueError(f"{label} must be finite, got {value!r}")
            if value < 0:
                raise ValueError(f"{label} must be non-negative, got {value!r}")
        if not math.isfinite(amount) or amount < 0 or amount > request_cap:
            return False
        today = datetime.now(UTC).date().isoformat()
        # Pre-check OUTSIDE the transaction for fast-path rejection. The
        # authoritative check happens inside `BEGIN IMMEDIATE` below — a
        # process that hits the cap between this pre-check and our own
        # transaction would otherwise let us insert a cost row for an
        # already-disabled session (Greptile #1 on commit 7ffd4c8).
        if self.is_paid_disabled(session_id):
            return False
        audit_id = str(uuid.uuid4())
        with _connect(self.path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            # In-transaction recheck of paid_disabled. Reading the
            # paid_disabled_sessions table inside BEGIN IMMEDIATE
            # guarantees we observe any commit by another process that
            # raced our pre-check. Without this recheck the
            # session_total query sees only committed `costs` rows —
            # which never include the racing process's failed-and-
            # rejected reservation — so a small `amount` can still pass
            # the session_cap test even though the session is already
            # paid-disabled.
            already_disabled = conn.execute(
                "SELECT 1 FROM paid_disabled_sessions WHERE session_id = ? LIMIT 1",
                (session_id,),
            ).fetchone()
            if already_disabled is not None:
                conn.execute("ROLLBACK")
                return False
            session_total = conn.execute(
                "SELECT COALESCE(SUM(estimated_cost_usd), 0) FROM costs WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
            daily_total = conn.execute(
                "SELECT COALESCE(SUM(estimated_cost_usd), 0) FROM costs WHERE day = ?",
                (today,),
            ).fetchone()[0]
            if session_total + amount > session_cap:
                # Mark session as paid-disabled so subsequent attempts short-circuit.
                conn.execute(
                    "INSERT OR REPLACE INTO paid_disabled_sessions(session_id, reason, created_at) VALUES (?, ?, ?)",
                    (session_id, "session_cap_exceeded", datetime.now(UTC).isoformat()),
                )
                conn.execute("COMMIT")
                return False
            if daily_total + amount > daily_cap:
                conn.execute("ROLLBACK")
                return False
            conn.execute(
                "INSERT INTO costs(audit_id, session_id, day, provider, estimated_cost_usd, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (audit_id, session_id, today, provider, amount, datetime.now(UTC).isoformat()),
            )
            conn.execute("COMMIT")
        self._mirror({
            "event": "reserve",
            "audit_id": audit_id,
            "session_id": session_id,
            "provider": provider,
            "amount_usd": amount,
            "day": today,
            "ts": datetime.now(UTC).isoformat(),
        })
        return audit_id

    def release(self, handle: str | int | None) -> bool:
        if not handle:
            return False
        with _connect(self.path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute("DELETE FROM costs WHERE audit_id = ?", (str(handle),))
            conn.execute("COMMIT")
            if cur.rowcount > 0:
                self._mirror({
                    "event": "release",
                    "audit_id": str(handle),
                    "ts": datetime.now(UTC).isoformat(),
                })
                return True
            return False

    def session_total(self, session_id: str) -> float:
        with _connect(self.path) as conn:
            return float(conn.execute(
                "SELECT COALESCE(SUM(estimated_cost_usd), 0) FROM costs WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0])

    def daily_total(self, day: str | None = None) -> float:
        day = day or datetime.now(UTC).date().isoformat()
        with _connect(self.path) as conn:
            return float(conn.execute(
                "SELECT COALESCE(SUM(estimated_cost_usd), 0) FROM costs WHERE day = ?",
                (day,),
            ).fetchone()[0])

    # --------- Paid-disabled sessions ---------------------------------------

    def disable_session(self, session_id: str, reason: str) -> None:
        with _connect(self.path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT OR REPLACE INTO paid_disabled_sessions(session_id, reason, created_at) VALUES (?, ?, ?)",
                (session_id, reason, datetime.now(UTC).isoformat()),
            )
            conn.execute("COMMIT")
        self._mirror({
            "event": "paid_disabled",
            "session_id": session_id,
            "reason": reason,
            "ts": datetime.now(UTC).isoformat(),
        })

    def is_paid_disabled(self, session_id: str) -> bool:
        with _connect(self.path) as conn:
            return conn.execute(
                "SELECT 1 FROM paid_disabled_sessions WHERE session_id = ? LIMIT 1",
                (session_id,),
            ).fetchone() is not None

    # --------- Audit mirror -------------------------------------------------

    def _mirror(self, event: dict[str, Any]) -> None:
        # Routes through the package-wide append helper which enforces the
        # full append-log invariant set: atomic line append, crash-safe
        # durability (fsync), 0o600 permission. Inlining these primitives
        # here previously omitted fsync — see persistence-contract
        # invariant C / r15-01.
        line = (json.dumps(event, sort_keys=True) + "\n").encode("utf-8")
        append_durable_line(self.mirror_path, line)


class TokenBucket:
    """Cross-process token bucket. Each call to `consume()` refills tokens
    based on elapsed wall-clock time, then atomically decrements one token if
    available."""

    def __init__(self, path: Path, provider: str, *, capacity: int, refill_seconds: float) -> None:
        # Fail loud at construction on misconfigured rate parameters.
        # `refill_seconds <= 0` would cause a ZeroDivisionError or
        # nonsensical refill in `consume()` and `retry_after_seconds()`
        # (Gemini #2 on commit 7ffd4c8). `capacity <= 0` would mean
        # the bucket can never serve a request, also not what any caller
        # actually wants. Both failures are operator config bugs that
        # should surface immediately, not silently mid-request.
        if capacity <= 0:
            raise ValueError(f"TokenBucket capacity must be > 0, got {capacity}")
        if refill_seconds <= 0:
            raise ValueError(
                f"TokenBucket refill_seconds must be > 0, got {refill_seconds}"
            )
        self.path = Path(path)
        self.provider = provider
        self.capacity = float(capacity)
        self.refill_seconds = float(refill_seconds)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _connect(self.path) as conn:
            _ensure_schema(conn)

    def consume(self, *, now: float | None = None) -> bool:
        now = now if now is not None else time.time()
        with _connect(self.path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT tokens, updated_at FROM token_buckets WHERE provider = ?",
                (self.provider,),
            ).fetchone()
            if row is None:
                tokens, updated_at = self.capacity, now
            else:
                tokens, updated_at = float(row[0]), float(row[1])
            elapsed = max(0.0, now - updated_at)
            tokens = min(self.capacity, tokens + elapsed * self.capacity / self.refill_seconds)
            if tokens >= 1.0:
                tokens -= 1.0
                conn.execute(
                    "INSERT INTO token_buckets(provider, tokens, updated_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(provider) DO UPDATE SET tokens=excluded.tokens, updated_at=excluded.updated_at",
                    (self.provider, tokens, now),
                )
                conn.execute("COMMIT")
                return True
            # Persist the refilled state so future callers see the new floor.
            conn.execute(
                "INSERT INTO token_buckets(provider, tokens, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(provider) DO UPDATE SET tokens=excluded.tokens, updated_at=excluded.updated_at",
                (self.provider, tokens, now),
            )
            conn.execute("COMMIT")
            return False

    def retry_after_seconds(self, *, now: float | None = None) -> float:
        now = now if now is not None else time.time()
        with _connect(self.path) as conn:
            row = conn.execute(
                "SELECT tokens, updated_at FROM token_buckets WHERE provider = ?",
                (self.provider,),
            ).fetchone()
        if row is None:
            return 0.0
        tokens, updated_at = float(row[0]), float(row[1])
        elapsed = max(0.0, now - updated_at)
        tokens = min(self.capacity, tokens + elapsed * self.capacity / self.refill_seconds)
        if tokens >= 1.0:
            return 0.0
        deficit = 1.0 - tokens
        return deficit * self.refill_seconds / self.capacity


class CircuitBreaker:
    """Session-scoped circuit breaker. Opens after CIRCUIT_BREAKER_THRESHOLD
    consecutive failures and stays open for CIRCUIT_BREAKER_OPEN_SECONDS."""

    def __init__(self, path: Path, session_id: str, provider: str) -> None:
        self.path = Path(path)
        self.session_id = session_id
        self.provider = provider
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _connect(self.path) as conn:
            _ensure_schema(conn)

    def is_open(self, *, now: float | None = None) -> bool:
        now = now if now is not None else time.time()
        with _connect(self.path) as conn:
            row = conn.execute(
                "SELECT consecutive_failures, opened_until FROM circuit_breakers WHERE session_id = ? AND provider = ?",
                (self.session_id, self.provider),
            ).fetchone()
        if not row:
            return False
        opened_until = row[1]
        return bool(opened_until and opened_until > now)

    def record_failure(self, *, now: float | None = None) -> bool:
        now = now if now is not None else time.time()
        with _connect(self.path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT consecutive_failures FROM circuit_breakers WHERE session_id = ? AND provider = ?",
                (self.session_id, self.provider),
            ).fetchone()
            failures = (row[0] if row else 0) + 1
            opened_until = (now + CIRCUIT_BREAKER_OPEN_SECONDS) if failures >= CIRCUIT_BREAKER_THRESHOLD else None
            conn.execute(
                "INSERT INTO circuit_breakers(session_id, provider, consecutive_failures, opened_until) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(session_id, provider) DO UPDATE SET consecutive_failures=excluded.consecutive_failures, opened_until=excluded.opened_until",
                (self.session_id, self.provider, failures, opened_until),
            )
            conn.execute("COMMIT")
            return opened_until is not None

    def record_success(self) -> None:
        with _connect(self.path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "DELETE FROM circuit_breakers WHERE session_id = ? AND provider = ?",
                (self.session_id, self.provider),
            )
            conn.execute("COMMIT")
