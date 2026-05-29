from __future__ import annotations

import asyncio
import sys
import types


class _Usage:
    def to_dict(self):
        return {"input_tokens": 10, "output_tokens": 5}


class _OpaqueUsage:
    def __repr__(self):
        return "<OpaqueUsage tokens=unknown>"


class _CircularUsage:
    def to_dict(self):
        data = {}
        data["self"] = data
        return data


class _Result:
    def __init__(self, *, success=True, message="Page title: Example Domain"):
        self.message = message
        self.success = success
        self.completed = success
        self.actions = [object()]
        self.usage = _Usage()


class _Response:
    def __init__(self, result):
        self.data = types.SimpleNamespace(result=result)


class _FakeSession:
    def __init__(self, events, result):
        self.id = "bb-session-1"
        self._events = events
        self._result = result

    async def execute(self, **kwargs):
        await asyncio.sleep(0)
        self._events.append(("execute", kwargs))
        return _Response(self._result)

    async def end(self):
        await asyncio.sleep(0)
        self._events.append(("end", {}))


class _FakeStagehand:
    def __init__(
        self,
        *,
        browserbase_api_key,
        browserbase_project_id,
        timeout,
        max_retries,
    ):
        self.browserbase_api_key = browserbase_api_key
        self.browserbase_project_id = browserbase_project_id
        self.timeout = timeout
        self.max_retries = max_retries
        self.events = []
        self.sessions = types.SimpleNamespace(start=self.start)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        self.events.append(("client_exit", {}))

    async def start(self, **kwargs):
        await asyncio.sleep(0)
        self.events.append(("start", kwargs))
        return _FakeSession(self.events, _Result())


def test_browserbase_stagehand_success_runs_and_ends_session(monkeypatch):
    from browser_fetch_router.providers import browserbase_stagehand

    created = []

    class TrackingStagehand(_FakeStagehand):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            created.append(self)

    monkeypatch.setitem(
        sys.modules,
        "stagehand",
        types.SimpleNamespace(AsyncStagehand=TrackingStagehand),
    )

    result = browserbase_stagehand.run_task(
        "Open https://example.com and report the page title",
        api_key="bb_secret",
        project_id="bb_project",
        model_name="google/gemini-2.5-flash",
        max_steps=3,
        max_duration_sec=30,
    )

    assert result["status"] == "ok"
    assert result["provider"] == "browserbase"
    assert result["content_markdown"] == "Page title: Example Domain"
    assert result["evidence"]["session_id"] == "bb-session-1"
    assert created
    assert created[0].browserbase_api_key == "bb_secret"
    assert created[0].browserbase_project_id == "bb_project"
    assert created[0].events == [
        ("start", {"model_name": "google/gemini-2.5-flash"}),
        ("execute", {
            "execute_options": {
                "instruction": "Open https://example.com and report the page title",
                "max_steps": 3,
            },
            "agent_config": {"model": "google/gemini-2.5-flash"},
            "timeout": 30.0,
        }),
        ("end", {}),
        ("client_exit", {}),
    ]


def test_browserbase_stagehand_whitespace_output_is_empty(monkeypatch):
    from browser_fetch_router.providers import browserbase_stagehand

    class WhitespaceSession(_FakeSession):
        async def execute(self, **kwargs):
            self._events.append(("execute", kwargs))
            return _Response(_Result(message=" \n\t "))

    class TrackingStagehand(_FakeStagehand):
        async def start(self, **kwargs):
            self.events.append(("start", kwargs))
            return WhitespaceSession(self.events, _Result())

    monkeypatch.setitem(
        sys.modules,
        "stagehand",
        types.SimpleNamespace(AsyncStagehand=TrackingStagehand),
    )

    result = browserbase_stagehand.run_task(
        "Open https://example.com and report the page title",
        api_key="bb_secret",
        project_id=None,
        model_name="google/gemini-2.5-flash",
        max_steps=3,
        max_duration_sec=30,
    )

    assert result["status"] == "provider_unavailable"
    assert result["error"]["code"] == "browserbase_empty_output"


def test_browserbase_stagehand_usage_evidence_is_json_safe(monkeypatch):
    from browser_fetch_router.providers import browserbase_stagehand

    class OpaqueUsageResult(_Result):
        def __init__(self):
            super().__init__()
            self.usage = _OpaqueUsage()

    class OpaqueUsageSession(_FakeSession):
        async def execute(self, **kwargs):
            self._events.append(("execute", kwargs))
            return _Response(OpaqueUsageResult())

    class TrackingStagehand(_FakeStagehand):
        async def start(self, **kwargs):
            self.events.append(("start", kwargs))
            return OpaqueUsageSession(self.events, _Result())

    monkeypatch.setitem(
        sys.modules,
        "stagehand",
        types.SimpleNamespace(AsyncStagehand=TrackingStagehand),
    )

    result = browserbase_stagehand.run_task(
        "Open https://example.com and report the page title",
        api_key="bb_secret",
        project_id=None,
        model_name="google/gemini-2.5-flash",
        max_steps=3,
        max_duration_sec=30,
    )

    assert result["status"] == "ok"
    assert result["evidence"]["usage"] == "<OpaqueUsage tokens=unknown>"


def test_browserbase_stagehand_usage_evidence_handles_json_value_error(monkeypatch):
    from browser_fetch_router.providers import browserbase_stagehand

    class CircularUsageResult(_Result):
        def __init__(self):
            super().__init__()
            self.usage = _CircularUsage()

    class CircularUsageSession(_FakeSession):
        async def execute(self, **kwargs):
            self._events.append(("execute", kwargs))
            return _Response(CircularUsageResult())

    class TrackingStagehand(_FakeStagehand):
        async def start(self, **kwargs):
            self.events.append(("start", kwargs))
            return CircularUsageSession(self.events, _Result())

    monkeypatch.setitem(
        sys.modules,
        "stagehand",
        types.SimpleNamespace(AsyncStagehand=TrackingStagehand),
    )

    result = browserbase_stagehand.run_task(
        "Open https://example.com and report the page title",
        api_key="bb_secret",
        project_id=None,
        model_name="google/gemini-2.5-flash",
        max_steps=3,
        max_duration_sec=30,
    )

    assert result["status"] == "ok"
    assert result["evidence"]["usage"] == "{'self': {...}}"


def test_browserbase_stagehand_failed_task_still_ends_session(monkeypatch):
    from browser_fetch_router.providers import browserbase_stagehand

    created = []

    class FailedSession(_FakeSession):
        async def execute(self, **kwargs):
            self._events.append(("execute", kwargs))
            return _Response(_Result(success=False, message="Could not complete task"))

    class TrackingStagehand(_FakeStagehand):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            created.append(self)

        async def start(self, **kwargs):
            self.events.append(("start", kwargs))
            return FailedSession(self.events, _Result())

    monkeypatch.setitem(
        sys.modules,
        "stagehand",
        types.SimpleNamespace(AsyncStagehand=TrackingStagehand),
    )

    result = browserbase_stagehand.run_task(
        "Open https://example.com and report the page title",
        api_key="bb_secret",
        project_id=None,
        model_name="google/gemini-2.5-flash",
        max_steps=3,
        max_duration_sec=30,
    )

    assert result["status"] == "provider_unavailable"
    assert result["error"]["code"] == "browserbase_task_failed"
    assert created[0].events[-2:] == [("end", {}), ("client_exit", {})]


def test_browserbase_stagehand_maps_auth_error(monkeypatch):
    from browser_fetch_router.providers import browserbase_stagehand

    class AuthError(Exception):
        status_code = 401

    class FailingStagehand(_FakeStagehand):
        async def start(self, **kwargs):
            raise AuthError("bad key")

    monkeypatch.setitem(
        sys.modules,
        "stagehand",
        types.SimpleNamespace(AsyncStagehand=FailingStagehand),
    )

    result = browserbase_stagehand.run_task(
        "Open https://example.com and report the page title",
        api_key="bb_secret",
        project_id=None,
        model_name="google/gemini-2.5-flash",
        max_steps=3,
        max_duration_sec=30,
    )

    assert result["status"] == "quota_or_key_missing"
    assert result["error"]["code"] == "browserbase_auth_failed"


def test_browserbase_stagehand_inside_event_loop_is_structured_without_warning(recwarn):
    from browser_fetch_router.providers import browserbase_stagehand

    async def call_provider():
        await asyncio.sleep(0)
        return browserbase_stagehand.run_task(
            "Open https://example.com and report the page title",
            api_key="bb_secret",
            project_id=None,
            model_name="google/gemini-2.5-flash",
            max_steps=3,
            max_duration_sec=30,
        )

    result = asyncio.run(call_provider())

    assert result["status"] == "provider_unavailable"
    assert result["error"]["code"] == "browserbase_event_loop_unavailable"
    assert not [
        warning
        for warning in recwarn
        if "was never awaited" in str(warning.message)
    ]
