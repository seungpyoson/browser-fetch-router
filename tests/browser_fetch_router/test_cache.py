from browser_fetch_router.cache import cache_key


def test_cache_key_is_deterministic():
    assert cache_key("jina-reader", "https://example.com/") == cache_key(
        "jina-reader", "https://example.com/"
    )


def test_cache_key_route_differentiation():
    assert cache_key("parallel", "https://example.com/") != cache_key(
        "jina-reader", "https://example.com/"
    )


def test_cache_key_url_differentiation():
    assert cache_key("jina-reader", "https://example.com/") != cache_key(
        "jina-reader", "https://example.com/page"
    )


# --- Task 12: cross-process cache + inflight lock ---------------------------


def test_cache_entry_uses_atomic_temp_then_rename(tmp_path):
    from browser_fetch_router.cache import CacheStore

    cache = CacheStore(tmp_path)
    cache.write("abc", {"status": "ok"}, ttl_seconds=60)
    assert cache.read("abc") == {"status": "ok"}
    # No leftover .tmp files in any subdirectory.
    leftover = list(tmp_path.rglob("*.tmp"))
    assert leftover == []


def test_cache_returns_none_after_ttl(tmp_path):
    from browser_fetch_router.cache import CacheStore

    cache = CacheStore(tmp_path)
    cache.write("abc", {"status": "ok"}, ttl_seconds=0)
    # ttl=0 → already expired.
    assert cache.read("abc") is None


def test_inflight_lock_is_filesystem_backed(tmp_path):
    from browser_fetch_router.cache import InflightLock

    first = InflightLock(tmp_path, "key")
    second = InflightLock(tmp_path, "key")
    assert first.acquire(timeout_seconds=0.1)
    assert not second.acquire(timeout_seconds=0.1)
    first.release()
    assert second.acquire(timeout_seconds=0.1)
    second.release()
