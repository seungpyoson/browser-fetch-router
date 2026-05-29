from __future__ import annotations

import json


class FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, object]) -> None:
        self.status_code = status_code
        self.text = json.dumps(payload)
        self.headers: dict[str, str] = {}


class FakeHttpClient:
    def __init__(self, responses: list[FakeResponse | Exception]) -> None:
        self.responses = responses
        self.calls: list[dict[str, object]] = []

    def request(self, method: str, url: str, **kwargs: object) -> FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        if not self.responses:
            raise AssertionError("unexpected extra request")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_browser_use_cloud_posts_safe_low_cost_session_and_polls_to_output():
    from browser_fetch_router.providers import browser_use_cloud

    client = FakeHttpClient([
        FakeResponse(200, {"id": "sess-1", "status": "created"}),
        FakeResponse(200, {"id": "sess-1", "status": "running"}),
        FakeResponse(
            200,
            {
                "id": "sess-1",
                "status": "stopped",
                "output": "Page title: Example Domain",
                "stepCount": 1,
                "totalCostUsd": "0.18",
            },
        ),
    ])

    result = browser_use_cloud.run_task(
        "open page https://example.com and report the title",
        api_key="bu_test",
        max_steps=3,
        max_duration_sec=30,
        max_cost_usd=0.25,
        http_client=client,
        sleep=lambda _seconds: None,
    )

    assert result["status"] == "ok"
    assert result["provider"] == "browser-use-cloud"
    assert result["content_markdown"] == "Page title: Example Domain"
    assert result["evidence"] == {
        "provider": "browser-use-cloud",
        "session_id": "sess-1",
        "remote_status": "stopped",
        "step_count": 1,
        "total_cost_usd": "0.18",
    }

    create = client.calls[0]
    assert create["method"] == "POST"
    assert create["url"] == "https://api.browser-use.com/api/v3/sessions"
    assert create["extra_headers"] == {
        "X-Browser-Use-API-Key": "bu_test",
        "Content-Type": "application/json",
    }
    assert isinstance(create["body"], bytes)
    body = json.loads(create["body"])
    assert body == {
        "task": "open page https://example.com and report the title",
        "model": "bu-mini",
        "keepAlive": False,
        "maxCostUsd": 0.25,
        "proxyCountryCode": None,
        "enableScheduledTasks": False,
        "enableRecording": False,
        "skills": False,
        "agentmail": False,
        "codeMode": False,
        "cacheScript": False,
        "useOwnKey": False,
        "autoHeal": False,
    }
    assert [call["method"] for call in client.calls[1:]] == ["GET", "GET"]
    assert client.calls[1]["url"] == "https://api.browser-use.com/api/v3/sessions/sess-1"


def test_browser_use_cloud_uses_last_step_summary_when_output_is_missing():
    from browser_fetch_router.providers import browser_use_cloud

    client = FakeHttpClient([
        FakeResponse(200, {"id": "sess-summary", "status": "running"}),
        FakeResponse(
            200,
            {
                "id": "sess-summary",
                "status": "stopped",
                "lastStepSummary": "Example Domain loaded successfully.",
                "stepCount": 1,
                "totalCostUsd": "0.01",
            },
        ),
    ])

    result = browser_use_cloud.run_task(
        "open page https://example.com and summarize it",
        api_key="bu_test",
        max_steps=3,
        max_duration_sec=30,
        max_cost_usd=0.25,
        http_client=client,
        sleep=lambda _seconds: None,
    )

    assert result["status"] == "ok"
    assert result["content_markdown"] == "Example Domain loaded successfully."
    assert result["evidence"]["remote_status"] == "stopped"
    assert [call["method"] for call in client.calls] == ["POST", "GET"]


def test_browser_use_cloud_empty_terminal_output_returns_provider_error():
    from browser_fetch_router.providers import browser_use_cloud

    client = FakeHttpClient([
        FakeResponse(
            200,
            {
                "id": "sess-empty",
                "status": "stopped",
                "stepCount": 0,
                "totalCostUsd": "0.00",
            },
        )
    ])

    result = browser_use_cloud.run_task(
        "open page https://example.com and report the title",
        api_key="bu_test",
        max_steps=3,
        max_duration_sec=30,
        max_cost_usd=0.25,
        http_client=client,
        sleep=lambda _seconds: None,
    )

    assert result["status"] == "provider_unavailable"
    assert result["error"]["code"] == "browser_use_cloud_empty_output"
    assert result["evidence"] == {
        "provider": "browser-use-cloud",
        "session_id": "sess-empty",
        "remote_status": "stopped",
        "step_count": 0,
        "total_cost_usd": "0.00",
    }
    assert [call["method"] for call in client.calls] == ["POST"]


def test_browser_use_cloud_maps_auth_failure_to_missing_quota_or_key():
    from browser_fetch_router.providers import browser_use_cloud

    client = FakeHttpClient([FakeResponse(401, {"detail": "bad key"})])

    result = browser_use_cloud.run_task(
        "open page https://example.com",
        api_key="bad",
        max_steps=3,
        max_duration_sec=30,
        max_cost_usd=0.25,
        http_client=client,
        sleep=lambda _seconds: None,
    )

    assert result["status"] == "quota_or_key_missing"
    assert result["error"]["code"] == "browser_use_cloud_auth_failed"
    assert result["error"]["http_status"] == 401


def test_browser_use_cloud_missing_session_id_returns_provider_error():
    from browser_fetch_router.providers import browser_use_cloud

    client = FakeHttpClient([FakeResponse(200, {"status": "created"})])

    result = browser_use_cloud.run_task(
        "open page https://example.com",
        api_key="bu_test",
        max_steps=3,
        max_duration_sec=30,
        max_cost_usd=0.25,
        http_client=client,
        sleep=lambda _seconds: None,
    )

    assert result["status"] == "provider_unavailable"
    assert result["error"]["code"] == "browser_use_cloud_missing_session_id"


def test_browser_use_cloud_stops_running_session_when_step_cap_is_reached():
    from browser_fetch_router.providers import browser_use_cloud

    client = FakeHttpClient([
        FakeResponse(200, {"id": "sess-steps", "status": "running", "stepCount": 1}),
        FakeResponse(200, {"id": "sess-steps", "status": "running", "stepCount": 3}),
        FakeResponse(
            200,
            {
                "id": "sess-steps",
                "status": "stopped",
                "stepCount": 3,
                "totalCostUsd": "0.07",
            },
        ),
    ])

    result = browser_use_cloud.run_task(
        "open page https://example.com",
        api_key="bu_test",
        max_steps=3,
        max_duration_sec=30,
        max_cost_usd=0.25,
        http_client=client,
        sleep=lambda _seconds: None,
    )

    assert result["status"] == "provider_unavailable"
    assert result["error"]["code"] == "browser_use_cloud_max_steps_exceeded"
    assert result["evidence"]["step_count"] == 3
    assert result["evidence"]["total_cost_usd"] == "0.07"
    assert [call["method"] for call in client.calls] == ["POST", "GET", "POST"]
    assert client.calls[2]["url"] == "https://api.browser-use.com/api/v3/sessions/sess-steps/stop"


def test_browser_use_cloud_stops_running_session_when_poll_fails():
    from browser_fetch_router.providers import browser_use_cloud

    client = FakeHttpClient([
        FakeResponse(200, {"id": "sess-poll-fail", "status": "running", "stepCount": 1}),
        RuntimeError("poll transport failed"),
        FakeResponse(
            200,
            {
                "id": "sess-poll-fail",
                "status": "stopped",
                "stepCount": 1,
                "totalCostUsd": "0.03",
            },
        ),
    ])

    result = browser_use_cloud.run_task(
        "open page https://example.com",
        api_key="bu_test",
        max_steps=3,
        max_duration_sec=30,
        max_cost_usd=0.25,
        http_client=client,
        sleep=lambda _seconds: None,
    )

    assert result["status"] == "provider_unavailable"
    assert result["error"]["code"] == "browser_use_cloud_poll_failed"
    assert result["evidence"]["remote_status"] == "stopped"
    assert result["evidence"]["total_cost_usd"] == "0.03"
    assert [call["method"] for call in client.calls] == ["POST", "GET", "POST"]
    assert client.calls[2]["url"] == "https://api.browser-use.com/api/v3/sessions/sess-poll-fail/stop"


def test_browser_use_cloud_stops_running_session_when_poll_returns_http_error():
    from browser_fetch_router.providers import browser_use_cloud

    client = FakeHttpClient([
        FakeResponse(200, {"id": "sess-poll-http-error", "status": "running", "stepCount": 1}),
        FakeResponse(500, {"detail": "temporary provider failure"}),
        FakeResponse(
            200,
            {
                "id": "sess-poll-http-error",
                "status": "stopped",
                "stepCount": 1,
                "totalCostUsd": "0.04",
            },
        ),
    ])

    result = browser_use_cloud.run_task(
        "open page https://example.com",
        api_key="bu_test",
        max_steps=3,
        max_duration_sec=30,
        max_cost_usd=0.25,
        http_client=client,
        sleep=lambda _seconds: None,
    )

    assert result["status"] == "provider_unavailable"
    assert result["error"]["code"] == "browser_use_cloud_http_error"
    assert result["error"]["http_status"] == 500
    assert result["error"]["detail"] == "temporary provider failure"
    assert result["evidence"]["remote_status"] == "stopped"
    assert result["evidence"]["total_cost_usd"] == "0.04"
    assert [call["method"] for call in client.calls] == ["POST", "GET", "POST"]
    assert (
        client.calls[2]["url"]
        == "https://api.browser-use.com/api/v3/sessions/sess-poll-http-error/stop"
    )


def test_browser_use_cloud_stops_running_session_when_deadline_is_reached(monkeypatch):
    from browser_fetch_router.providers import browser_use_cloud

    ticks = iter([0.0, 2.0])
    monkeypatch.setattr(browser_use_cloud.time, "monotonic", lambda: next(ticks))
    client = FakeHttpClient([
        FakeResponse(200, {"id": "sess-timeout", "status": "running", "stepCount": 1}),
        FakeResponse(
            200,
            {
                "id": "sess-timeout",
                "status": "stopped",
                "stepCount": 1,
                "totalCostUsd": "0.02",
            },
        ),
    ])

    result = browser_use_cloud.run_task(
        "open page https://example.com",
        api_key="bu_test",
        max_steps=3,
        max_duration_sec=1,
        max_cost_usd=0.25,
        http_client=client,
        sleep=lambda _seconds: None,
    )

    assert result["status"] == "provider_unavailable"
    assert result["error"]["code"] == "browser_use_cloud_timeout"
    assert result["evidence"]["remote_status"] == "stopped"
    assert result["evidence"]["total_cost_usd"] == "0.02"
    assert [call["method"] for call in client.calls] == ["POST", "POST"]
    assert client.calls[1]["url"] == "https://api.browser-use.com/api/v3/sessions/sess-timeout/stop"
