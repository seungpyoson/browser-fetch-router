from browser_fetch_router.read_web import classify_route


def test_x_routes_to_fxtwitter():
    assert classify_route("https://x.com/jack/status/20") == "fxtwitter"
    assert classify_route("https://twitter.com/jack/status/20") == "fxtwitter"


def test_reddit_routes_to_reddit_json():
    assert classify_route("https://www.reddit.com/r/redditdev/comments/abc/title/") == "reddit-json"
    assert classify_route("https://old.reddit.com/r/python/comments/abc/title/") == "reddit-json"
    assert classify_route("https://notreddit.com/r/python/comments/abc/title/") == "jina-reader"


def test_generic_routes_to_jina():
    assert classify_route("https://example.com/") == "jina-reader"


# --- Task 13: live provider orchestration with mocked fetches ---------------


def test_read_web_uses_fxtwitter_for_x(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    from browser_fetch_router import read_web as module

    monkeypatch.setattr(
        module,
        "fetch_fxtwitter",
        lambda url, ctx: {
            "status": "ok",
            "title": "@jack",
            "content_markdown": "hello from x",
            "provider": "fxtwitter",
            "route": "fxtwitter",
            "evidence": {},
            "error": None,
        },
    )
    payload = module.read_web("https://x.com/jack/status/20")
    assert payload["status"] == "ok"
    assert payload["route"] == "fxtwitter"
    assert payload["content_markdown"] == "hello from x"


def test_parallel_requires_allow_paid(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    from browser_fetch_router import read_web as module

    monkeypatch.setattr(
        module,
        "fetch_jina",
        lambda url, ctx: {
            "status": "insufficient_content",
            "provider": "jina-reader",
            "route": "jina-reader",
            "evidence": {},
            "error": {"code": "jina_low_quality"},
        },
    )
    payload = module.read_web("https://example.com/", allow_paid=False, no_cache=True)
    assert payload["status"] == "quota_or_key_missing"
    assert payload["error"]["code"] == "paid_fallback_not_allowed"


def test_content_is_truncated_with_indicator(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    from browser_fetch_router import read_web as module

    monkeypatch.setattr(
        module,
        "fetch_jina",
        lambda url, ctx: {
            "status": "ok",
            "content_markdown": "x" * 100,
            "provider": "jina-reader",
            "route": "jina-reader",
            "evidence": {},
            "error": None,
        },
    )
    payload = module.read_web("https://example.com/", max_chars=10, no_cache=True)
    assert payload["content_markdown"].endswith("[TRUNCATED after 10 chars]")
    assert payload["evidence"]["truncated"] is True
    assert payload["evidence"]["original_chars"] == 100


def test_unsafe_url_handler_propagates_safety_error(monkeypatch, tmp_path):
    """read_web() now propagates SafetyError out — the CLI dispatcher
    (`cli._emit`) is the single place that converts it to an
    `unsafe_url_blocked` envelope. Direct callers must catch SafetyError
    themselves. End-to-end CLI behavior is covered by
    `test_cli_dispatcher_*`."""
    import pytest
    monkeypatch.setenv("HOME", str(tmp_path))
    from browser_fetch_router import read_web as module
    from browser_fetch_router.url_safety import SafetyError

    with pytest.raises(SafetyError):
        module.read_web("http://127.0.0.1/")


def test_blocked_signal_sets_next_path(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    from browser_fetch_router import read_web as module

    monkeypatch.setattr(
        module,
        "fetch_jina",
        lambda url, ctx: {
            "status": "blocked_needs_browser",
            "provider": "jina-reader",
            "route": "jina-reader",
            "evidence": {},
            "error": {"code": "jina_blocked_signal"},
        },
    )
    payload = module.read_web("https://example.com/", no_cache=True)
    assert payload["status"] == "blocked_needs_browser"
    assert payload["next_path"] == "interactive-browser"


# --- Task 13: env_allowlist ------------------------------------------------


def test_provider_env_drops_agent_keys(monkeypatch):
    from browser_fetch_router.env_allowlist import provider_env

    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret-anthropic")
    monkeypatch.setenv("OPENAI_API_KEY", "secret-openai")
    monkeypatch.setenv("GEMINI_API_KEY", "secret-gemini")
    monkeypatch.setenv("GOOGLE_API_KEY", "secret-google")
    monkeypatch.setenv("PARALLEL_API_KEY", "secret-parallel")
    monkeypatch.setenv("HOME", "/tmp/home")
    env = provider_env({"PARALLEL_API_KEY"})
    assert env.get("PARALLEL_API_KEY") == "secret-parallel"
    assert "ANTHROPIC_API_KEY" not in env
    assert "OPENAI_API_KEY" not in env
    assert "GEMINI_API_KEY" not in env
    assert "GOOGLE_API_KEY" not in env
    assert env.get("HOME") == "/tmp/home"


def test_provider_env_refuses_to_pass_blocked_keys_even_if_requested(monkeypatch):
    from browser_fetch_router.env_allowlist import provider_env

    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
    env = provider_env({"ANTHROPIC_API_KEY"})  # caller misbehaving
    assert "ANTHROPIC_API_KEY" not in env


# --- Internal-review WARNING #1 fix: _finalize must not mutate input -------


def test_finalize_does_not_mutate_caller_evidence(monkeypatch, tmp_path):
    """_finalize must deep-copy its input. Mutating the caller's evidence
    dict in place would corrupt cached envelopes shared across reads."""
    monkeypatch.setenv("HOME", str(tmp_path))
    from browser_fetch_router import read_web as module

    original_evidence = {"provider_url": "https://example.com/"}
    original_payload = {
        "schema_version": "browser-fetch-router.v1",
        "command": "read-web",
        "status": "ok",
        "url": "https://example.com/",
        "route": "jina-reader",
        "provider": "jina-reader",
        "title": "Example",
        "content_markdown": "x" * 30,
        "evidence": original_evidence,
        "error": None,
    }
    out = module._finalize(
        original_payload,
        "https://example.com/",
        "jina-reader",
        max_chars=10,
        cached_hit=True,
        session_id="s1",
        invoking_agent="claude",
    )
    # Output is decorated.
    assert out["evidence"]["cached"] is True
    assert out["evidence"]["session_id"] == "s1"
    # Input untouched.
    assert original_evidence == {"provider_url": "https://example.com/"}
    assert "cached" not in original_evidence
    assert original_payload["content_markdown"] == "x" * 30  # not truncated


# --- Internal-review WARNING #3 fix: cache stores un-truncated payload -----


def test_cache_stores_untruncated_so_larger_max_chars_re_reads_full(
    monkeypatch, tmp_path
):
    """A second call with a LARGER max_chars must see the full content,
    not the previously-truncated content + stale `[TRUNCATED]` suffix."""
    monkeypatch.setenv("HOME", str(tmp_path))
    from browser_fetch_router import read_web as module

    long_content = "abc" * 1000  # 3000 chars

    monkeypatch.setattr(
        module,
        "fetch_jina",
        lambda url, ctx: {
            "status": "ok",
            "content_markdown": long_content,
            "provider": "jina-reader",
            "route": "jina-reader",
            "evidence": {},
            "error": None,
        },
    )
    # First call: small max_chars triggers truncation.
    first = module.read_web("https://example.com/article", max_chars=100)
    assert first["evidence"]["truncated"] is True
    assert first["content_markdown"].endswith("[TRUNCATED after 100 chars]")
    assert first["evidence"]["original_chars"] == len(long_content)

    # Second call: HIT cache, larger max_chars, must NOT show truncation suffix.
    second = module.read_web("https://example.com/article", max_chars=10_000)
    assert second["evidence"]["cached"] is True
    assert second["evidence"]["truncated"] is False
    assert second["content_markdown"] == long_content
    assert "[TRUNCATED" not in second["content_markdown"]


def test_cache_does_not_record_session_id_in_evidence(monkeypatch, tmp_path):
    """Cached envelopes must NOT carry the original caller's session_id;
    each read should stamp its own session_id."""
    monkeypatch.setenv("HOME", str(tmp_path))
    from browser_fetch_router import read_web as module

    monkeypatch.setattr(
        module,
        "fetch_jina",
        lambda url, ctx: {
            "status": "ok",
            "content_markdown": "hello world content",
            "provider": "jina-reader",
            "route": "jina-reader",
            "evidence": {},
            "error": None,
        },
    )
    first = module.read_web("https://example.com/article", session_id="alice")
    assert first["evidence"]["session_id"] == "alice"
    assert first["evidence"]["cached"] is False

    # Second call with a different session_id reads from cache but stamps the
    # NEW session_id, not the cached one.
    second = module.read_web("https://example.com/article", session_id="bob")
    assert second["evidence"]["cached"] is True
    assert second["evidence"]["session_id"] == "bob"
