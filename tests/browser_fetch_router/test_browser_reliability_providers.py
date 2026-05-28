from browser_fetch_router.providers import jina


def _example_domain_jina_text() -> str:
    return "\n".join(
        [
            "Title: Example Domain",
            "",
            "# Example Domain",
            "",
            "This domain is for use in illustrative examples in documents.",
            "You may use this domain in literature without prior coordination or asking for permission.",
            "",
            "[More information...](https://www.iana.org/domains/example)",
        ]
    )


class _Response:
    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.text = text


class _TextClient:
    def __init__(self, text: str) -> None:
        self.text = text
        self.requested: list[str] = []

    def get_text(self, url: str, *, max_bytes: int) -> _Response:
        self.requested.append(url)
        return _Response(200, self.text)


def test_jina_accepts_short_valid_public_page_content():
    client = _TextClient(_example_domain_jina_text())

    result = jina.fetch("https://example.com/", {"http_client": client})

    assert result["status"] == "ok"
    assert result["title"] == "Title: Example Domain"
    assert "illustrative examples" in result["content_markdown"]
    assert result["evidence"]["quality"]["is_short_valid_content"] is True
    assert client.requested == ["https://r.jina.ai/https://example.com/"]


def test_read_web_accepts_short_valid_public_page_content(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    from browser_fetch_router import read_web as module

    payload = module.read_web(
        "https://example.com/",
        no_cache=True,
        http_client=_TextClient(_example_domain_jina_text()),
    )

    assert payload["status"] == "ok"
    assert payload["provider"] == "jina-reader"
    assert payload["title"] == "Title: Example Domain"
    assert "illustrative examples" in payload["content_markdown"]
    assert payload["quality"]["is_short_valid_content"] is True
