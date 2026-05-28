from __future__ import annotations


def test_local_interactive_browser_reports_provider_unavailable(monkeypatch):
    from browser_fetch_router import interactive

    monkeypatch.setattr(interactive, "_local_browser_use_available", lambda: True)

    result = interactive.run_interactive_browser("read the current page", provider="local")

    assert result["status"] == "tool_setup_failed"
    assert result["error"]["code"] == "provider_unavailable"
    assert "launch_pending" not in result["error"].get("message", "")


def test_local_unavailable_suggests_browserbase_when_browserbase_creds_present(monkeypatch):
    from browser_fetch_router import interactive

    monkeypatch.setattr(interactive, "_local_browser_use_available", lambda: False)
    monkeypatch.setenv("BROWSERBASE_API_KEY", "present")
    monkeypatch.setenv("BROWSERBASE_PROJECT_ID", "present")
    monkeypatch.delenv("BROWSER_USE_API_KEY", raising=False)

    result = interactive.run_interactive_browser("read the current page", provider="local")

    assert result["status"] == "tool_setup_failed"
    assert result["error"]["code"] == "provider_unavailable"
    assert result["evidence"]["provider"] == "local"
    assert result["evidence"]["suggested_provider"] == "browserbase"


def test_local_unavailable_suggests_cloud_when_only_cloud_creds_present(monkeypatch):
    from browser_fetch_router import interactive

    monkeypatch.setattr(interactive, "_local_browser_use_available", lambda: False)
    monkeypatch.delenv("BROWSERBASE_API_KEY", raising=False)
    monkeypatch.delenv("BROWSERBASE_PROJECT_ID", raising=False)
    monkeypatch.setenv("BROWSER_USE_API_KEY", "present")

    result = interactive.run_interactive_browser("read the current page", provider="local")

    assert result["status"] == "tool_setup_failed"
    assert result["error"]["code"] == "provider_unavailable"
    assert result["evidence"]["provider"] == "local"
    assert result["evidence"]["suggested_provider"] == "browser-use-cloud"


def test_browserbase_after_opt_in_reports_provider_unavailable(monkeypatch):
    from browser_fetch_router import interactive

    monkeypatch.setenv("BROWSERBASE_API_KEY", "present")
    monkeypatch.setenv("BROWSERBASE_PROJECT_ID", "present")

    result = interactive.run_interactive_browser(
        "read the current page",
        provider="browserbase",
        allow_hosted_browser=True,
    )

    assert result["status"] == "tool_setup_failed"
    assert result["error"]["code"] == "provider_unavailable"
    assert result["evidence"]["provider"] == "browserbase"


def test_bare_open_url_is_tier_a():
    from browser_fetch_router.interactive import classify_action

    assert classify_action("Open https://example.com and report the page title") == "A"


def test_cloud_after_opt_in_missing_key_reports_quota_or_key_missing(monkeypatch):
    from browser_fetch_router import interactive

    monkeypatch.delenv("BROWSER_USE_API_KEY", raising=False)

    result = interactive.run_interactive_browser(
        "read the current page",
        provider="cloud",
        allow_hosted_browser=True,
    )

    assert result["status"] == "quota_or_key_missing"
    assert result["error"]["code"] == "browser_use_cloud_key_missing"


def test_cloud_after_opt_in_dispatches_browser_use_cloud_with_cost_guard(tmp_path, monkeypatch):
    from browser_fetch_router import interactive
    from browser_fetch_router.cost import CostLedger
    from browser_fetch_router.providers import browser_use_cloud

    calls = []

    def fake_run_task(**kwargs):
        calls.append(kwargs)
        return {
            "status": "ok",
            "provider": "browser-use-cloud",
            "content_markdown": "Page title: Example Domain",
            "evidence": {
                "provider": "browser-use-cloud",
                "session_id": "remote-1",
                "remote_status": "stopped",
                "step_count": 1,
                "total_cost_usd": "0.18",
            },
        }

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BFR_SESSION_ID", "bfr-cloud-ok")
    monkeypatch.setenv("BROWSER_USE_API_KEY", "bu_secret")
    monkeypatch.setattr(browser_use_cloud, "run_task", fake_run_task)

    result = interactive.run_interactive_browser(
        "Open https://example.com and report the page title",
        provider="cloud",
        allow_hosted_browser=True,
        max_steps=3,
        max_duration_sec=30,
        max_cost_usd=0.25,
    )

    assert result["status"] == "ok"
    assert result["command"] == "interactive-browser"
    assert result["provider"] == "browser-use-cloud"
    assert result["content_markdown"] == "Page title: Example Domain"
    assert calls == [{
        "task": "Open https://example.com and report the page title",
        "api_key": "bu_secret",
        "max_steps": 3,
        "max_duration_sec": 30,
        "max_cost_usd": 0.25,
    }]

    ledger = CostLedger(tmp_path / ".local" / "state" / "browser-fetch-router" / "cost.db")
    assert ledger.session_total("bfr-cloud-ok") == 0.18


def test_cloud_provider_overrun_returns_cost_cap_and_disables_session(tmp_path, monkeypatch):
    from browser_fetch_router import interactive
    from browser_fetch_router.cost import CostLedger
    from browser_fetch_router.providers import browser_use_cloud

    calls = []

    def fake_run_task(**kwargs):
        calls.append(kwargs)
        return {
            "status": "ok",
            "provider": "browser-use-cloud",
            "content_markdown": "over budget output",
            "evidence": {
                "provider": "browser-use-cloud",
                "session_id": "remote-overrun",
                "remote_status": "stopped",
                "step_count": 1,
                "total_cost_usd": "0.30",
            },
        }

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BFR_SESSION_ID", "bfr-cloud-overrun")
    monkeypatch.setenv("BROWSER_USE_API_KEY", "bu_secret")
    monkeypatch.setattr(browser_use_cloud, "run_task", fake_run_task)

    result = interactive.run_interactive_browser(
        "open page https://example.com and report the title",
        provider="cloud",
        allow_hosted_browser=True,
        max_cost_usd=0.25,
    )

    assert result["status"] == "cost_cap_exceeded"
    assert result["error"]["code"] == "cost_cap_exceeded"
    assert result["evidence"]["reported_total_cost_usd"] == "0.30"
    assert result["evidence"]["max_cost_usd"] == 0.25
    assert len(calls) == 1

    result = interactive.run_interactive_browser(
        "open page https://example.com and report the title",
        provider="cloud",
        allow_hosted_browser=True,
        max_cost_usd=0.25,
    )

    assert result["status"] == "cost_cap_exceeded"
    assert len(calls) == 1
    ledger = CostLedger(tmp_path / ".local" / "state" / "browser-fetch-router" / "cost.db")
    assert ledger.is_paid_disabled("bfr-cloud-overrun")


def test_cloud_failed_provider_result_still_enforces_reported_overrun(tmp_path, monkeypatch):
    from browser_fetch_router import interactive
    from browser_fetch_router.cost import CostLedger
    from browser_fetch_router.providers import browser_use_cloud

    def fake_run_task(**_kwargs):
        return {
            "status": "provider_unavailable",
            "provider": "browser-use-cloud",
            "error": {"code": "browser_use_cloud_timed_out"},
            "evidence": {
                "provider": "browser-use-cloud",
                "session_id": "remote-timeout",
                "remote_status": "timed_out",
                "step_count": 2,
                "total_cost_usd": "0.31",
            },
        }

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BFR_SESSION_ID", "bfr-cloud-failed-overrun")
    monkeypatch.setenv("BROWSER_USE_API_KEY", "bu_secret")
    monkeypatch.setattr(browser_use_cloud, "run_task", fake_run_task)

    result = interactive.run_interactive_browser(
        "open page https://example.com and report the title",
        provider="cloud",
        allow_hosted_browser=True,
        max_cost_usd=0.25,
    )

    assert result["status"] == "cost_cap_exceeded"
    assert result["evidence"]["reported_total_cost_usd"] == "0.31"
    ledger = CostLedger(tmp_path / ".local" / "state" / "browser-fetch-router" / "cost.db")
    assert ledger.is_paid_disabled("bfr-cloud-failed-overrun")
