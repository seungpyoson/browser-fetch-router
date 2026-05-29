from __future__ import annotations

import copy
import sys
from typing import Any
from urllib.parse import urlsplit

from browser_fetch_router.cache import CacheStore, InflightLock, ROUTE_TTLS, cache_key
from browser_fetch_router.http_client import SafeHttpClient, SideEffectPolicy
from browser_fetch_router.paths import cache_dir
from browser_fetch_router.providers import fxtwitter as _fxtwitter
from browser_fetch_router.providers import jina as _jina
from browser_fetch_router.providers import parallel as _parallel
from browser_fetch_router.providers import reddit as _reddit
from browser_fetch_router.schema import envelope
from browser_fetch_router.url_safety import normalize_and_validate_url

X_HOSTS = {"x.com", "twitter.com", "www.x.com", "www.twitter.com", "mobile.twitter.com", "m.twitter.com"}
REDDIT_HOSTS = {"reddit.com", "www.reddit.com", "old.reddit.com", "np.reddit.com"}

# Module-level fetch functions so tests can monkeypatch them.
fetch_fxtwitter = _fxtwitter.fetch
fetch_reddit = _reddit.fetch
fetch_jina = _jina.fetch
fetch_parallel = _parallel.fetch


def classify_route(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    if host in X_HOSTS:
        return "fxtwitter"
    if host in REDDIT_HOSTS:
        return "reddit-json"
    return "jina-reader"


def read_web(
    url: str,
    *,
    allow_paid: bool = False,
    no_cache: bool = False,
    strict_side_effects: bool = False,
    allow_side_effects: bool = False,
    max_chars: int = 50_000,
    session_id: str | None = None,
    invoking_agent: str | None = None,
    http_client: SafeHttpClient | None = None,
) -> dict[str, Any]:
    """Route a URL to its provider, apply quality + truncation, cache, audit.

    Any UnsafeUrl raised anywhere in this orchestration is converted to an
    `unsafe_url_blocked` envelope (exit code 4). Safety errors must not
    escape as `internal_error`.
    """
    # SafetyError propagates from this function. The CLI dispatch layer
    # (`cli._safe_dispatch`) catches all exceptions and converts them into
    # structured envelopes. Local try/except is reserved for cases that need
    # a richer envelope than the dispatcher produces.
    normalized = normalize_and_validate_url(url)
    route = classify_route(normalized)
    side_effect_policy = SideEffectPolicy(
        strict=strict_side_effects, allow=allow_side_effects
    )
    ctx: dict[str, Any] = {
        "session_id": session_id,
        "invoking_agent": invoking_agent,
        "allow_paid": allow_paid,
        "side_effect_policy": side_effect_policy,
        "http_client": http_client or SafeHttpClient(),
        "http_client_is_default": http_client is None,
    }

    cache = CacheStore(cache_dir() / "web")
    key = cache_key(route, normalized)

    if not no_cache:
        cached = cache.read(key)
        if cached is not None:
            return _finalize(
                cached, normalized, route, max_chars,
                cached_hit=True, session_id=session_id,
                invoking_agent=invoking_agent,
            )

    lock = InflightLock(cache_dir() / "web", key)
    try:
        lock.acquire(timeout_seconds=5.0)
        # Recheck cache after lock — another process may have populated it.
        if not no_cache:
            cached = cache.read(key)
            if cached is not None:
                return _finalize(
                    cached, normalized, route, max_chars,
                    cached_hit=True, session_id=session_id,
                    invoking_agent=invoking_agent,
                )
        primary = _dispatch_primary(route, normalized, ctx)
        result = _maybe_paid_fallback(primary, normalized, route, ctx)
    finally:
        lock.release()

    payload = _shape_envelope(result, normalized, route)
    # Cache the SHAPED envelope BEFORE truncation/session decoration so
    # subsequent reads with a different `max_chars` truncate fresh and
    # session_id/cached/invoking_agent are not stamped into the cache.
    if payload.get("status") == "ok" and not no_cache:
        # TTL must reflect the PROVIDER that produced the result, not the
        # URL classification route. When jina-reader returns
        # `insufficient_content` and `--allow-paid` triggers Parallel,
        # the cached result is a paid Parallel response — caching it
        # under the jina TTL (600 s) means we re-call paid Parallel 6×
        # more often than its native 3600 s TTL would (round-6 r6-g05).
        # Cache key stays keyed on classification route so subsequent
        # reads for the same URL deduplicate; only the TTL adjusts.
        provider_used = payload.get("provider")
        ttl = ROUTE_TTLS.get(provider_used, ROUTE_TTLS.get(route, 300))
        try:
            cache.write(key, payload, ttl_seconds=ttl)
        except OSError as exc:
            # Cache writes are best-effort — a disk-full or
            # cross-device-rename failure must NOT fail the request.
            # But silent swallowing leaves operators blind to cache
            # degradation, which causes duplicate paid-provider charges
            # on the next request. Surface to stderr so an operator
            # tailing logs sees the failure even when the JSON envelope
            # on stdout reports success.
            sys.stderr.write(
                f"[bfr] warning: cache_write_failed route={route} key={key[:8]}…: "
                f"{type(exc).__name__}: {exc}\n"
            )
    return _finalize(
        payload, normalized, route, max_chars,
        cached_hit=False, session_id=session_id,
        invoking_agent=invoking_agent,
    )


def _dispatch_primary(route: str, url: str, ctx: dict[str, Any]) -> dict[str, Any]:
    if route == "fxtwitter":
        return fetch_fxtwitter(url, ctx)
    if route == "reddit-json":
        return fetch_reddit(url, ctx)
    return fetch_jina(url, ctx)


def _maybe_paid_fallback(primary: dict[str, Any], url: str, route: str, ctx: dict[str, Any]) -> dict[str, Any]:
    """For generic web (jina-reader): if primary fails quality (insufficient_content)
    Parallel can be tried with --allow-paid. blocked_needs_browser/auth_required/
    captcha route to interactive-browser instead. Reddit/X never fall back to
    Parallel in v1."""
    if route != "jina-reader":
        return primary
    status = primary.get("status")
    # Only "insufficient_content" is a candidate for paid extraction. Browser-
    # required statuses route to interactive-browser, not Parallel.
    if status != "insufficient_content":
        return primary
    if not ctx.get("allow_paid"):
        return {
            **primary,
            "status": "quota_or_key_missing",
            "error": {
                "code": "paid_fallback_not_allowed",
                "message": "Jina returned low-quality content. Pass --allow-paid to try Parallel.",
                "primary_status": status,
            },
        }
    paid_ctx = ctx
    if ctx.get("http_client_is_default"):
        paid_ctx = {**ctx}
        paid_ctx.pop("http_client", None)
        paid_ctx.pop("http_client_is_default", None)
    return fetch_parallel(url, paid_ctx)


def _shape_envelope(result: dict[str, Any], url: str, route: str) -> dict[str, Any]:
    return envelope(
        command="read-web",
        status=result.get("status", "internal_error"),
        url=url,
        route=route,
        provider=result.get("provider"),
        title=result.get("title"),
        content_markdown=result.get("content_markdown"),
        evidence=result.get("evidence"),
        quality=(result.get("evidence") or {}).get("quality"),
        error=result.get("error"),
    )


def _finalize(
    payload: dict[str, Any],
    url: str,
    route: str,
    max_chars: int,
    *,
    cached_hit: bool,
    session_id: str | None,
    invoking_agent: str | None,
) -> dict[str, Any]:
    """Decorate a shaped envelope with per-call session/cached/audit fields
    and apply truncation. Deep-copies the input so the caller's payload
    (especially a cached envelope shared with subsequent reads) is never
    mutated."""
    payload = copy.deepcopy(payload)
    if payload.get("evidence") is None:
        payload["evidence"] = {}
    payload["evidence"]["cached"] = cached_hit
    payload["evidence"]["session_id"] = session_id
    payload["evidence"]["invoking_agent"] = invoking_agent
    content = payload.get("content_markdown")
    if isinstance(content, str) and max_chars > 0 and len(content) > max_chars:
        original = len(content)
        payload["content_markdown"] = content[:max_chars] + f"\n\n[TRUNCATED after {max_chars} chars]"
        payload["evidence"]["truncated"] = True
        payload["evidence"]["original_chars"] = original
    else:
        payload["evidence"]["truncated"] = False
    if payload.get("status") in {"blocked_needs_browser", "captcha_required", "auth_required"}:
        payload["next_path"] = "interactive-browser"
    # Audit emission moved to cli._emit dispatcher (single source of truth).
    # The dispatcher reads route + cached + session_id + invoking_agent
    # from the returned envelope's `route` field and `evidence` block, so
    # no per-handler audit call is needed.
    return payload
