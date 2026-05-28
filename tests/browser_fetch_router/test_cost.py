from browser_fetch_router.cost import CostLedger


def test_cost_cap_blocks_second_charge(tmp_path):
    ledger = CostLedger(tmp_path / "cost.sqlite3")
    assert ledger.reserve(
        "session-1",
        "parallel",
        0.04,
        request_cap=0.05,
        session_cap=0.05,
        daily_cap=1.00,
    )
    assert not ledger.reserve(
        "session-1",
        "parallel",
        0.02,
        request_cap=0.05,
        session_cap=0.05,
        daily_cap=1.00,
    )


def test_request_cap_blocks_oversized_charge(tmp_path):
    ledger = CostLedger(tmp_path / "cost.sqlite3")
    assert not ledger.reserve(
        "s1", "parallel", 0.10, request_cap=0.05, session_cap=1.00, daily_cap=10.00
    )


def test_daily_cap_blocks_across_sessions(tmp_path):
    ledger = CostLedger(tmp_path / "cost.sqlite3")
    assert ledger.reserve(
        "s1", "parallel", 0.04, request_cap=0.05, session_cap=0.05, daily_cap=0.05
    )
    # New session, but daily cap exhausted.
    assert not ledger.reserve(
        "s2", "parallel", 0.02, request_cap=0.05, session_cap=0.05, daily_cap=0.05
    )


def test_release_rolls_back_reservation(tmp_path):
    ledger = CostLedger(tmp_path / "cost.sqlite3")
    handle = ledger.reserve(
        "s1", "parallel", 0.04, request_cap=0.05, session_cap=0.05, daily_cap=1.00
    )
    assert handle is not False
    ledger.release(handle)
    # After release, room exists again.
    assert ledger.reserve(
        "s1", "parallel", 0.04, request_cap=0.05, session_cap=0.05, daily_cap=1.00
    )


def test_session_cap_lockout_persists(tmp_path):
    """Once a session blows the session cap, subsequent reservations are
    locked out via paid_disabled_sessions even if a release would free room."""
    ledger = CostLedger(tmp_path / "cost.sqlite3")
    # First reservation fills near the cap.
    assert ledger.reserve(
        "s1", "parallel", 0.04, request_cap=0.05, session_cap=0.05, daily_cap=10.0
    )
    # Second reservation tries to exceed cap → blocked AND session marked.
    assert not ledger.reserve(
        "s1", "parallel", 0.04, request_cap=0.05, session_cap=0.05, daily_cap=10.0
    )
    assert ledger.is_paid_disabled("s1")
    # Even a tiny later charge is now blocked for this session.
    assert not ledger.reserve(
        "s1", "parallel", 0.001, request_cap=0.05, session_cap=10.0, daily_cap=10.0
    )


def test_disable_session_blocks_future_reservations(tmp_path):
    ledger = CostLedger(tmp_path / "cost.sqlite3")

    ledger.disable_session("s1", "provider_overrun")

    assert ledger.is_paid_disabled("s1")
    assert not ledger.reserve(
        "s1", "browser-use-cloud", 0.01, request_cap=0.25, session_cap=1.0, daily_cap=10.0
    )


# --- Task 12: token bucket + circuit breaker -------------------------------


def test_token_bucket_is_shared_across_instances(tmp_path):
    from browser_fetch_router.cost import TokenBucket

    a = TokenBucket(tmp_path / "rate.sqlite3", "reddit-json", capacity=1, refill_seconds=2)
    b = TokenBucket(tmp_path / "rate.sqlite3", "reddit-json", capacity=1, refill_seconds=2)
    # Drain the shared bucket via a; b sees zero.
    assert a.consume(now=100.0)
    assert not b.consume(now=100.0)


def test_token_bucket_refills_with_time(tmp_path):
    from browser_fetch_router.cost import TokenBucket

    bucket = TokenBucket(tmp_path / "rate.sqlite3", "fxtwitter", capacity=1, refill_seconds=1)
    assert bucket.consume(now=100.0)
    assert not bucket.consume(now=100.5)
    # After full refill window, a token is available.
    assert bucket.consume(now=101.5)


def test_circuit_breaker_opens_after_failures(tmp_path):
    from browser_fetch_router.cost import CircuitBreaker

    cb = CircuitBreaker(tmp_path / "rate.sqlite3", "s1", "parallel")
    assert not cb.is_open()
    cb.record_failure()
    cb.record_failure()
    assert not cb.is_open()
    cb.record_failure()  # third failure → open
    assert cb.is_open()


def test_circuit_breaker_clears_on_success(tmp_path):
    from browser_fetch_router.cost import CircuitBreaker

    cb = CircuitBreaker(tmp_path / "rate.sqlite3", "s1", "parallel")
    cb.record_failure()
    cb.record_failure()
    cb.record_success()
    cb.record_failure()
    cb.record_failure()
    # Only 2 failures since last success → still closed.
    assert not cb.is_open()


def test_circuit_breaker_is_session_scoped(tmp_path):
    from browser_fetch_router.cost import CircuitBreaker

    cb1 = CircuitBreaker(tmp_path / "rate.sqlite3", "s1", "parallel")
    cb2 = CircuitBreaker(tmp_path / "rate.sqlite3", "s2", "parallel")
    cb1.record_failure()
    cb1.record_failure()
    cb1.record_failure()
    assert cb1.is_open()
    assert not cb2.is_open()


# --- External-review (Gemini #2): _connect closes connections deterministically


def test_connect_is_contextmanager_and_closes_after_with_block(tmp_path):
    """Regression for Gemini #2. `with sqlite3.connect(...) as conn:` does
    NOT close the connection — sqlite3's __exit__ only commits/rollbacks.
    Across many invocations that leaks file descriptors. The fix wraps
    `_connect` in @contextmanager so __exit__ forces close()."""
    import sqlite3

    import pytest

    from browser_fetch_router import cost

    db = tmp_path / "ledger.sqlite3"
    cm = cost._connect(db)
    assert hasattr(cm, "__enter__")
    assert hasattr(cm, "__exit__")
    with cm as conn:
        assert conn.execute("SELECT 1").fetchone() == (1,)
        held = conn
    # After exit, the connection must be closed — using it raises.
    with pytest.raises(sqlite3.ProgrammingError):
        held.execute("SELECT 1")


def test_many_ledger_operations_do_not_accumulate_state(tmp_path):
    """Light proof that the contextmanager pattern works across repeated
    open/close cycles. Doesn't measure FDs directly (would be flaky on CI)
    — exercises reserve/release/session_total many times to confirm none
    of them leave stale state behind."""
    ledger = CostLedger(tmp_path / "ledger.sqlite3")
    for i in range(50):
        h = ledger.reserve(
            f"sess-{i}", "parallel", 0.001,
            request_cap=0.05, session_cap=0.05, daily_cap=10.0,
        )
        assert h
        ledger.release(h)
        assert ledger.session_total(f"sess-{i}") == 0.0
