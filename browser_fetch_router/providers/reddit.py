from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from browser_fetch_router.http_client import SafeHttpClient
from browser_fetch_router.url_safety import SafetyError


def json_url(reddit_url: str) -> str:
    parsed = urlsplit(reddit_url)
    path = parsed.path.rstrip("/")
    if not path.endswith(".json"):
        path = f"{path}.json"
    query = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if k.lower() != "limit"
    ]
    query.append(("limit", "3"))
    # Force www.reddit.com to avoid old.reddit.com session inheritance.
    netloc = "www.reddit.com" if parsed.hostname else parsed.netloc
    return urlunsplit((parsed.scheme, netloc, path, urlencode(query), parsed.fragment))


def fetch(url: str, ctx: dict[str, Any]) -> dict[str, Any]:
    client: SafeHttpClient = ctx.get("http_client") or SafeHttpClient()
    target = json_url(url)
    try:
        response = client.get_text(target, max_bytes=2_000_000)
    except SafetyError:
        raise
    except Exception as exc:
        return _result("provider_unavailable", url=target, error={"code": "reddit_request_failed", "message": str(exc)[:200]})
    if response.status_code == 401:
        return _result("auth_required", url=target, error={"code": "reddit_auth_required"})
    if response.status_code == 403:
        return _result("blocked", url=target, error={"code": "reddit_forbidden", "subtype": "private_or_quarantined"})
    if response.status_code == 404:
        return _result("private_or_deleted", url=target, error={"code": "reddit_not_found"})
    if response.status_code == 429:
        return _result("rate_limited", url=target, error={"code": "reddit_rate_limited"})
    if response.status_code >= 500:
        return _result("provider_unavailable", url=target, error={"code": "reddit_5xx", "http_status": response.status_code})
    if response.status_code >= 400:
        return _result("provider_unavailable", url=target, error={"code": "reddit_http_error", "http_status": response.status_code})
    try:
        data = json.loads(response.text)
    except json.JSONDecodeError:
        return _result("provider_unavailable", url=target, error={"code": "reddit_invalid_json"})
    md = _shape_reddit_listing(data)
    if not md:
        return _result("insufficient_content", url=target, error={"code": "reddit_empty_listing"})
    title = _extract_title(data)
    return _result("ok", url=target, title=title, content_markdown=md)


def _extract_title(data: Any) -> str | None:
    if isinstance(data, list) and data:
        post = ((data[0] or {}).get("data") or {}).get("children", [])
        if post and isinstance(post, list):
            return ((post[0] or {}).get("data") or {}).get("title")
    if isinstance(data, dict):
        children = (data.get("data") or {}).get("children") or []
        if children and isinstance(children, list):
            return ((children[0] or {}).get("data") or {}).get("title")
    return None


def _shape_reddit_listing(data: Any) -> str:
    """Shape post + top comments into markdown."""
    lines: list[str] = []
    if isinstance(data, dict):
        children = (data.get("data") or {}).get("children") or []
        if isinstance(children, list):
            subreddit = _first_text(children, "subreddit")
            heading = f"# r/{subreddit}" if subreddit else "# Reddit listing"
            post_lines: list[str] = []
            for child in children[:5]:
                post = (child or {}).get("data", {}) or {}
                title = post.get("title")
                if not title:
                    continue
                author = post.get("author") or "unknown"
                score = post.get("score")
                comments = post.get("num_comments")
                permalink = post.get("permalink")
                meta = [f"u/{author}"]
                if score is not None:
                    meta.append(f"score {score}")
                if comments is not None:
                    meta.append(f"{comments} comments")
                post_lines.append(f"\n## {title}")
                post_lines.append(f"{' | '.join(meta)}")
                if permalink:
                    post_lines.append(f"https://www.reddit.com{permalink}")
                selftext = post.get("selftext") or ""
                if selftext:
                    post_lines.append(selftext)
            if post_lines:
                lines.append(heading)
                lines.extend(post_lines)
            return "\n".join(lines).strip()
    if isinstance(data, list) and len(data) >= 1:
        post_node = (data[0] or {}).get("data", {}).get("children", [])
        if post_node:
            post = (post_node[0] or {}).get("data", {})
            title = post.get("title")
            selftext = post.get("selftext") or ""
            if title:
                lines.append(f"# {title}\n")
            if selftext:
                lines.append(selftext + "\n")
        if len(data) >= 2:
            comments_node = (data[1] or {}).get("data", {}).get("children", [])
            for comment in comments_node[:5]:
                cdata = (comment or {}).get("data", {}) or {}
                body = cdata.get("body")
                author = cdata.get("author") or "unknown"
                if body:
                    lines.append(f"\n**{author}**: {body}\n")
    return "\n".join(lines).strip()


def _first_text(children: list[Any], key: str) -> str | None:
    for child in children:
        value = ((child or {}).get("data") or {}).get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _result(status: str, *, url: str, title: str | None = None, content_markdown: str | None = None, error: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "status": status,
        "title": title,
        "content_markdown": content_markdown,
        "provider": "reddit-json",
        "route": "reddit-json",
        "evidence": {"provider_url": url},
        "error": error,
    }
