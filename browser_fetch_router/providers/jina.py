from __future__ import annotations

from typing import Any

from browser_fetch_router.http_client import SafeHttpClient
from browser_fetch_router.quality import assess_quality
from browser_fetch_router.url_safety import SafetyError


def reader_url(url: str) -> str:
    return f"https://r.jina.ai/{url}"


def fetch(url: str, ctx: dict[str, Any]) -> dict[str, Any]:
    client: SafeHttpClient = ctx.get("http_client") or SafeHttpClient()
    target = reader_url(url)
    try:
        response = client.get_text(target, max_bytes=10_000_000)
    except SafetyError:
        raise
    except Exception as exc:
        return _result("provider_unavailable", url=target, error={"code": "jina_request_failed", "message": str(exc)[:200]})
    if response.status_code in {429, 503}:
        return _result("provider_unavailable", url=target, error={"code": "jina_rate_limited", "http_status": response.status_code})
    if response.status_code == 404:
        return _result("private_or_deleted", url=target, error={"code": "jina_not_found"})
    if response.status_code >= 400:
        return _result("provider_unavailable", url=target, error={"code": "jina_http_error", "http_status": response.status_code})
    text = response.text
    quality = assess_quality(text, text)  # Jina returns markdown; raw_html≈text.
    blocked = quality.get("blocked_signals") or []
    if "captcha_required" in blocked or "js_challenge" in blocked:
        return _result(
            "blocked_needs_browser",
            url=target,
            error={"code": "jina_blocked_signal", "signals": blocked},
        )
    if "login_wall" in blocked:
        return _result(
            "auth_required",
            url=target,
            error={"code": "jina_login_wall"},
        )
    if not quality.get("passes_quality_gate"):
        return _result(
            "insufficient_content",
            url=target,
            error={"code": "jina_low_quality", "quality": quality},
        )
    title = _extract_first_line(text) if text else None
    return _result("ok", url=target, title=title, content_markdown=text, quality=quality)


def _extract_first_line(text: str) -> str | None:
    for line in text.splitlines():
        line = line.strip("# \t")
        if line:
            return line[:200]
    return None


def _result(status: str, *, url: str, title: str | None = None, content_markdown: str | None = None, error: dict[str, Any] | None = None, quality: dict[str, Any] | None = None) -> dict[str, Any]:
    out = {
        "status": status,
        "title": title,
        "content_markdown": content_markdown,
        "provider": "jina-reader",
        "route": "jina-reader",
        "evidence": {"provider_url": url},
        "error": error,
    }
    if quality is not None:
        out["evidence"]["quality"] = quality
    return out
