import pytest

from browser_fetch_router.url_safety import UnsafeUrl, normalize_and_validate_url


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/",
        "javascript:alert(1)",
        "http://localhost/",
        "http://0/",
        "http://0.0.0.0/",
        "http://127.0.0.1/",
        "http://127.1/",
        "http://0177.0.0.1/",
        "http://0x7f.0.0.1/",
        "http://127.0.0.01/",
        "http://127.000.000.001/",
        "http://example.com:22/",
        "http://example.com:25/",
        "http://0x7f000001/",
        "http://2130706433/",
        "http://017700000001/",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://[fc00::1]/",
        "http://[fe80::1%25en0]/",
        "http://169.254.169.254/latest/meta-data/",
        "http://user:pass@example.com/",
    ],
)
def test_unsafe_urls_are_blocked(url):
    with pytest.raises(UnsafeUrl):
        normalize_and_validate_url(url)


def test_safe_https_url_normalizes_trailing_slash():
    result = normalize_and_validate_url("https://example.com")
    assert result == "https://example.com/"


def test_twitter_alias_normalizes_to_x():
    result = normalize_and_validate_url("https://twitter.com/jack/status/20")
    assert result.startswith("https://x.com/")


def test_old_reddit_alias_normalizes_to_www_reddit():
    result = normalize_and_validate_url("https://old.reddit.com/r/python/")
    assert result.startswith("https://www.reddit.com/")


# --- Task 11: DNS pinning, redirects, side-effect policy --------------------

from browser_fetch_router.http_client import (
    SideEffectPolicy,
    side_effect_warning,
    should_block_side_effect_redirect,
)
from browser_fetch_router.url_safety import (
    ResolvedTarget,
    blocked_resolved_targets,
    validate_redirect,
)


def test_any_private_dns_answer_blocks_hostname():
    answers = [
        ResolvedTarget(hostname="mixed.example", ip="93.184.216.34", family="AF_INET"),
        ResolvedTarget(hostname="mixed.example", ip="10.0.0.4", family="AF_INET"),
    ]
    assert blocked_resolved_targets(answers) == "blocked_resolved_ip"


def test_empty_dns_answer_is_blocked():
    assert blocked_resolved_targets([]) == "dns_resolution_empty"


def test_redirect_to_private_host_is_revalidated():
    with pytest.raises(UnsafeUrl):
        validate_redirect("https://example.com/a", "http://127.0.0.1/internal")


def test_https_to_http_redirect_is_blocked_by_default(monkeypatch):
    monkeypatch.delenv("BFR_ALLOW_HTTPS_DOWNGRADE", raising=False)
    with pytest.raises(UnsafeUrl):
        validate_redirect("https://example.com/a", "http://example.com/a")


def test_https_to_http_redirect_allowed_with_env(monkeypatch):
    monkeypatch.setenv("BFR_ALLOW_HTTPS_DOWNGRADE", "true")
    out = validate_redirect("https://example.com/a", "http://example.com/a")
    assert out.startswith("http://example.com/")


def test_redirect_to_metadata_blocked():
    with pytest.raises(UnsafeUrl):
        validate_redirect(
            "https://example.com/a",
            "http://169.254.169.254/latest/meta-data/",
        )


def test_side_effect_warning_does_not_block_path_alone():
    policy = SideEffectPolicy(strict=False, allow=False)
    assert (
        should_block_side_effect_redirect(
            "https://example.com/docs/delete-bucket.html", policy
        )
        is False
    )


def test_side_effect_redirect_with_token_is_blocked():
    policy = SideEffectPolicy(strict=False, allow=False)
    assert (
        should_block_side_effect_redirect(
            "https://example.com/unsubscribe?token=abc123", policy
        )
        is True
    )


def test_strict_side_effect_mode_blocks_action_path():
    policy = SideEffectPolicy(strict=True, allow=False)
    assert (
        should_block_side_effect_redirect("https://example.com/delete/item", policy)
        is True
    )


def test_direct_side_effect_request_warns_by_default():
    policy = SideEffectPolicy(strict=False, allow=False)
    assert (
        side_effect_warning("https://example.com/unsubscribe?confirm=true", policy)
        == "side_effect_like_url"
    )


def test_direct_side_effect_request_blocks_in_strict_mode():
    policy = SideEffectPolicy(strict=True, allow=False)
    assert (
        should_block_side_effect_redirect(
            "https://example.com/unsubscribe?confirm=true", policy
        )
        is True
    )


def test_allow_side_effects_overrides_block():
    policy = SideEffectPolicy(strict=True, allow=True)
    assert (
        should_block_side_effect_redirect(
            "https://example.com/delete/x?token=abc", policy
        )
        is False
    )


# --- External-review (Greptile #1): CRLF injection via URL path/query -----


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/foo\r\nX-Injected: evil",
        "http://example.com/foo\nX-Injected: evil",
        "http://example.com/foo?bar\r\nX-Injected: evil",
        "http://example.com/foo?bar=\r\nX-Injected: evil",
        "http://example.com/foo#frag\r\ninjected",
        "http://example.com/foo\x00bar",
    ],
)
def test_url_with_control_chars_in_path_query_or_fragment_is_rejected(url):
    """Regression for Greptile #1. Without this guard, `urlsplit` preserves
    CR/LF/NUL in path/query/fragment; the transport then interpolates the
    bytes directly into the request line and the CRLF splits the line,
    injecting an arbitrary header on the wire. Mirrors the existing
    `_validate_extra_headers` guard for caller-supplied headers."""
    with pytest.raises(UnsafeUrl):
        normalize_and_validate_url(url)


# --- External-review (Gemini round 2 #M2): URL request-target must be
# percent-encoded so the wire bytes are RFC-3986-compliant ASCII. Without
# encoding, a URL like `https://example.com/café` produced a request line
# carrying raw `é` (0xE9) which the latin-1 encoder happened to accept but
# the spec forbids — and chars > 0xFF (em-dashes, CJK) crashed
# UnicodeEncodeError. Class fix: encode at the URL-validation seam so the
# wire boundary is unable to crash by construction.


def test_normalize_percent_encodes_non_ascii_path():
    """`é` (U+00E9) → %C3%A9 (UTF-8 bytes percent-encoded)."""
    out = normalize_and_validate_url("https://example.com/café")
    assert "café" not in out
    assert "%C3%A9" in out


def test_normalize_percent_encodes_non_ascii_query():
    """Same for query strings."""
    out = normalize_and_validate_url("https://example.com/?q=café")
    assert "café" not in out
    assert "%C3%A9" in out


def test_normalize_does_not_double_encode_existing_percent_sequences():
    """If the caller already passed percent-encoded sequences, they must
    NOT be re-encoded (`%2520` is the bug signature). The `safe='...%'` set
    keeps `%` from being itself percent-encoded."""
    pre_encoded = "https://example.com/%C3%A9"
    out = normalize_and_validate_url(pre_encoded)
    assert "%C3%A9" in out
    assert "%2520" not in out
    assert "%25C3" not in out


def test_normalize_percent_encodes_em_dash_unicode():
    """An em-dash (U+2014, 3 UTF-8 bytes) must round-trip through the
    encoder without crashing — class fix for the previous latin-1 boundary
    that crashed on chars > 0xFF."""
    out = normalize_and_validate_url("https://example.com/before—after")
    assert "—" not in out  # raw em-dash gone
    assert "%E2%80%94" in out  # UTF-8 of em-dash, percent-encoded
