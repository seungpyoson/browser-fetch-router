from __future__ import annotations

import json


class FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, object]) -> None:
        self.status_code = status_code
        self.text = json.dumps(payload)
        self.headers: dict[str, str] = {}


class FakeHttpClient:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[dict[str, object]] = []

    def request(self, method: str, url: str, **kwargs: object) -> FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        if not self.responses:
            raise AssertionError("unexpected extra request")
        return self.responses.pop(0)


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
