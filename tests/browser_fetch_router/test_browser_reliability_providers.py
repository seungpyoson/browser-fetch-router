import json

from browser_fetch_router.providers import jina
from browser_fetch_router.providers import parallel


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


class _RequestClient:
    def __init__(self, response: _Response) -> None:
        self.response = response
        self.requests: list[dict[str, object]] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        body: bytes,
        max_bytes: int,
        extra_headers: dict[str, str],
    ) -> _Response:
        self.requests.append(
            {
                "method": method,
                "url": url,
                "body": body,
                "max_bytes": max_bytes,
                "extra_headers": extra_headers,
            }
        )
        return self.response


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


def test_read_web_paid_fallback_uses_parallel_extract_v1_response(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PARALLEL_API_KEY", "test-parallel-key")
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
    client = _RequestClient(
        _Response(
            200,
            json.dumps(
                {
                    "extract_id": "extract_123",
                    "results": [
                        {
                            "url": "https://example.com/article",
                            "title": "Example Article",
                            "full_content": "# Example Article\n\nRecovered by paid extract.",
                        }
                    ],
                    "errors": [],
                    "session_id": "session_123",
                }
            ),
        )
    )

    payload = module.read_web(
        "https://example.com/article",
        allow_paid=True,
        no_cache=True,
        http_client=client,
    )

    assert payload["status"] == "ok"
    assert payload["provider"] == "parallel"
    assert payload["route"] == "jina-reader"
    assert payload["title"] == "Example Article"
    assert "Recovered by paid extract" in payload["content_markdown"]
    assert client.requests[0]["url"] == "https://api.parallel.ai/v1/extract"


def test_read_web_paid_fallback_uses_parallel_specific_default_timeout(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PARALLEL_API_KEY", "test-parallel-key")
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

    class _DefaultPrimaryClient:
        def request(self, *_args, **_kwargs) -> _Response:
            raise AssertionError("generic primary client must not run paid fallback")

    captured: dict[str, float] = {}

    class _ParallelTimeoutClient:
        def __init__(self, timeout: float = 30.0) -> None:
            captured["timeout"] = timeout

        def request(self, *_args, **_kwargs) -> _Response:
            return _Response(
                200,
                json.dumps(
                    {
                        "extract_id": "extract_123",
                        "results": [
                            {
                                "url": "https://example.com/article",
                                "excerpts": ["Recovered by paid extract."],
                            }
                        ],
                        "errors": [],
                        "session_id": "session_123",
                    }
                ),
            )

    monkeypatch.setattr(module, "SafeHttpClient", _DefaultPrimaryClient)
    monkeypatch.setattr(parallel, "SafeHttpClient", _ParallelTimeoutClient)

    payload = module.read_web(
        "https://example.com/article",
        allow_paid=True,
        no_cache=True,
    )

    assert payload["status"] == "ok"
    assert payload["provider"] == "parallel"
    assert captured["timeout"] >= 60.0


def test_parallel_extract_uses_v1_api_shape_and_reads_excerpts(monkeypatch):
    monkeypatch.setenv("PARALLEL_API_KEY", "test-parallel-key")
    client = _RequestClient(
        _Response(
            200,
            json.dumps(
                {
                    "extract_id": "extract_123",
                    "results": [
                        {
                            "url": "https://example.com/article",
                            "title": "Example Article",
                            "excerpts": [
                                "First relevant excerpt.",
                                "Second relevant excerpt.",
                            ],
                        }
                    ],
                    "errors": [],
                    "session_id": "session_123",
                }
            ),
        )
    )

    result = parallel.fetch(
        "https://example.com/article",
        {"allow_paid": True, "http_client": client},
    )

    assert result["status"] == "ok"
    assert result["title"] == "Example Article"
    assert (
        result["content_markdown"]
        == "First relevant excerpt.\n\nSecond relevant excerpt."
    )
    assert result["provider"] == "parallel"
    assert result["route"] == "parallel"

    assert len(client.requests) == 1
    request = client.requests[0]
    assert request["method"] == "POST"
    assert request["url"] == "https://api.parallel.ai/v1/extract"
    assert request["extra_headers"] == {
        "x-api-key": "test-parallel-key",
        "Content-Type": "application/json",
    }
    body = json.loads(request["body"])
    assert body["urls"] == ["https://example.com/article"]
    assert isinstance(body["objective"], str)
    assert "Extract" in body["objective"]


def test_parallel_extract_strips_full_content(monkeypatch):
    monkeypatch.setenv("PARALLEL_API_KEY", "test-parallel-key")
    client = _RequestClient(
        _Response(
            200,
            json.dumps(
                {
                    "extract_id": "extract_123",
                    "results": [
                        {
                            "url": "https://example.com/article",
                            "title": "Example Article",
                            "full_content": "\n\n# Example Article\n\nRecovered by paid extract.\n\n",
                        }
                    ],
                    "errors": [],
                    "session_id": "session_123",
                }
            ),
        )
    )

    result = parallel.fetch(
        "https://example.com/article",
        {"allow_paid": True, "http_client": client},
    )

    assert result["status"] == "ok"
    assert result["content_markdown"] == "# Example Article\n\nRecovered by paid extract."


def test_parallel_extract_uses_provider_specific_timeout(monkeypatch):
    monkeypatch.setenv("PARALLEL_API_KEY", "test-parallel-key")
    captured: dict[str, float] = {}

    class _ParallelTimeoutClient:
        def __init__(self, timeout: float = 30.0) -> None:
            captured["timeout"] = timeout

        def request(self, *_args, **_kwargs) -> _Response:
            return _Response(
                200,
                json.dumps(
                    {
                        "extract_id": "extract_123",
                        "results": [
                            {
                                "url": "https://example.com/article",
                                "excerpts": ["Recovered by paid extract."],
                            }
                        ],
                        "errors": [],
                        "session_id": "session_123",
                    }
                ),
            )

    monkeypatch.setattr(parallel, "SafeHttpClient", _ParallelTimeoutClient)

    result = parallel.fetch(
        "https://example.com/article",
        {"allow_paid": True},
    )

    assert result["status"] == "ok"
    assert captured["timeout"] >= 60.0


def test_parallel_extract_preserves_v1_validation_error_details(monkeypatch):
    monkeypatch.setenv("PARALLEL_API_KEY", "test-parallel-key")
    client = _RequestClient(
        _Response(
            422,
            json.dumps(
                {
                    "type": "error",
                    "error": {
                        "ref_id": "extract_123",
                        "message": "Request validation error",
                    },
                }
            ),
        )
    )

    result = parallel.fetch(
        "https://example.com/article",
        {"allow_paid": True, "http_client": client},
    )

    assert result["status"] == "provider_unavailable"
    assert result["error"] == {
        "code": "parallel_http_error",
        "http_status": 422,
        "message": "Request validation error",
        "ref_id": "extract_123",
    }


def test_parallel_extract_maps_missing_key_without_request(monkeypatch):
    monkeypatch.delenv("PARALLEL_API_KEY", raising=False)
    client = _RequestClient(_Response(200, "{}"))

    result = parallel.fetch(
        "https://example.com/article",
        {"allow_paid": True, "http_client": client},
    )

    assert result["status"] == "quota_or_key_missing"
    assert result["error"] == {"code": "parallel_key_missing"}
    assert client.requests == []


def test_parallel_extract_maps_rate_limit(monkeypatch):
    monkeypatch.setenv("PARALLEL_API_KEY", "test-parallel-key")
    client = _RequestClient(
        _Response(
            429,
            json.dumps(
                {
                    "type": "error",
                    "error": {
                        "ref_id": "extract_123",
                        "message": "Rate limit exceeded",
                    },
                }
            ),
        )
    )

    result = parallel.fetch(
        "https://example.com/article",
        {"allow_paid": True, "http_client": client},
    )

    assert result["status"] == "rate_limited"
    assert result["error"] == {
        "code": "parallel_rate_limited",
        "http_status": 429,
        "message": "Rate limit exceeded",
        "ref_id": "extract_123",
    }


def test_parallel_extract_maps_auth_failure(monkeypatch):
    monkeypatch.setenv("PARALLEL_API_KEY", "test-parallel-key")
    client = _RequestClient(
        _Response(
            401,
            json.dumps(
                {
                    "type": "error",
                    "error": {
                        "ref_id": "extract_123",
                        "message": "Invalid API key",
                    },
                }
            ),
        )
    )

    result = parallel.fetch(
        "https://example.com/article",
        {"allow_paid": True, "http_client": client},
    )

    assert result["status"] == "quota_or_key_missing"
    assert result["error"] == {
        "code": "parallel_auth_failed",
        "http_status": 401,
        "message": "Invalid API key",
        "ref_id": "extract_123",
    }


def test_parallel_extract_rejects_invalid_json(monkeypatch):
    monkeypatch.setenv("PARALLEL_API_KEY", "test-parallel-key")
    client = _RequestClient(_Response(200, "{not json"))

    result = parallel.fetch(
        "https://example.com/article",
        {"allow_paid": True, "http_client": client},
    )

    assert result["status"] == "provider_unavailable"
    assert result["error"] == {"code": "parallel_invalid_json"}


def test_parallel_extract_rejects_malformed_success_json_shape(monkeypatch):
    monkeypatch.setenv("PARALLEL_API_KEY", "test-parallel-key")
    client = _RequestClient(_Response(200, "[]"))

    result = parallel.fetch(
        "https://example.com/article",
        {"allow_paid": True, "http_client": client},
    )

    assert result["status"] == "provider_unavailable"
    assert result["error"] == {"code": "parallel_invalid_json"}


def test_parallel_extract_maps_empty_result_without_url_error(monkeypatch):
    monkeypatch.setenv("PARALLEL_API_KEY", "test-parallel-key")
    client = _RequestClient(
        _Response(
            200,
            json.dumps(
                {
                    "extract_id": "extract_123",
                    "results": [],
                    "errors": [],
                    "session_id": "session_123",
                }
            ),
        )
    )

    result = parallel.fetch(
        "https://example.com/article",
        {"allow_paid": True, "http_client": client},
    )

    assert result["status"] == "insufficient_content"
    assert result["error"] == {"code": "parallel_empty_response"}


def test_parallel_extract_preserves_v1_url_error_details(monkeypatch):
    monkeypatch.setenv("PARALLEL_API_KEY", "test-parallel-key")
    client = _RequestClient(
        _Response(
            200,
            json.dumps(
                {
                    "extract_id": "extract_123",
                    "results": [],
                    "errors": [
                        {
                            "url": "https://example.com/article",
                            "error_type": "fetch_error",
                            "http_status_code": 500,
                            "content": "Error fetching content from target",
                        }
                    ],
                    "session_id": "session_123",
                }
            ),
        )
    )

    result = parallel.fetch(
        "https://example.com/article",
        {"allow_paid": True, "http_client": client},
    )

    assert result["status"] == "provider_unavailable"
    assert result["error"] == {
        "code": "parallel_extract_error",
        "error_type": "fetch_error",
        "http_status": 500,
        "message": "Error fetching content from target",
    }
