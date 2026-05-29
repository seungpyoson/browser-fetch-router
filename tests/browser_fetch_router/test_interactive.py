from __future__ import annotations


import builtins
import sys
import types

import pytest


def test_local_interactive_browser_is_not_a_daily_provider(monkeypatch):
    from browser_fetch_router import interactive

    result = interactive.run_interactive_browser("read the current page", provider="local")

    assert result["status"] == "usage_error"
    assert result["error"]["code"] == "provider_not_advertised"
    assert result["error"]["provider"] == "local"


def test_default_provider_selects_browserbase_when_only_browserbase_creds_present(tmp_path, monkeypatch):
    from browser_fetch_router import interactive

    calls = []

    def fake_run_task(**kwargs):
        calls.append(kwargs)
        return {
            "status": "ok",
            "provider": "browserbase",
            "content_markdown": "Page title: Example Domain",
            "evidence": {"provider": "browserbase", "session_id": "bb-1"},
        }

    fake_module = types.SimpleNamespace(run_task=fake_run_task)
    monkeypatch.setitem(
        sys.modules,
        "browser_fetch_router.providers.browserbase_stagehand",
        fake_module,
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BFR_SESSION_ID", "bfr-browserbase-default")
    monkeypatch.setenv("BROWSERBASE_API_KEY", "bb_secret")
    monkeypatch.delenv("BROWSER_USE_API_KEY", raising=False)

    result = interactive.run_interactive_browser(
        "Open https://example.com and report the page title",
        allow_hosted_browser=True,
        max_steps=4,
        max_duration_sec=30,
        max_cost_usd=0.25,
    )

    assert result["status"] == "ok"
    assert result["provider"] == "browserbase"
    assert calls == [{
        "task": "Open https://example.com and report the page title",
        "api_key": "bb_secret",
        "project_id": None,
        "model_name": "google/gemini-2.5-flash",
        "max_steps": 4,
        "max_duration_sec": 30,
    }]


def test_default_provider_selects_cloud_before_browserbase_when_cloud_creds_present(tmp_path, monkeypatch):
    from browser_fetch_router import interactive
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
                "session_id": "bu-1",
                "total_cost_usd": "0.01",
            },
        }

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BFR_SESSION_ID", "bfr-cloud-default")
    monkeypatch.setenv("BROWSERBASE_API_KEY", "bb_secret")
    monkeypatch.setenv("BROWSER_USE_API_KEY", "bu_secret")
    monkeypatch.setattr(browser_use_cloud, "run_task", fake_run_task)

    result = interactive.run_interactive_browser(
        "Open https://example.com and report the page title",
        allow_hosted_browser=True,
    )

    assert result["status"] == "ok"
    assert result["provider"] == "browser-use-cloud"
    assert calls[0]["api_key"] == "bu_secret"


def test_browserbase_after_opt_in_dispatches_stagehand_with_cost_guard(tmp_path, monkeypatch):
    from browser_fetch_router import interactive
    from browser_fetch_router.cost import CostLedger

    calls = []

    def fake_run_task(**kwargs):
        calls.append(kwargs)
        return {
            "status": "ok",
            "provider": "browserbase",
            "content_markdown": "Page title: Example Domain",
            "evidence": {"provider": "browserbase", "session_id": "bb-explicit"},
        }

    fake_module = types.SimpleNamespace(run_task=fake_run_task)
    monkeypatch.setitem(
        sys.modules,
        "browser_fetch_router.providers.browserbase_stagehand",
        fake_module,
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BFR_SESSION_ID", "bfr-browserbase-ok")
    monkeypatch.setenv("BROWSERBASE_API_KEY", "bb_secret")
    monkeypatch.setenv("BROWSERBASE_PROJECT_ID", "bb_project")

    result = interactive.run_interactive_browser(
        "Open https://example.com and report the page title",
        provider="browserbase",
        allow_hosted_browser=True,
        max_steps=3,
        max_duration_sec=30,
        max_cost_usd=0.25,
    )

    assert result["status"] == "ok"
    assert result["provider"] == "browserbase"
    assert result["content_markdown"] == "Page title: Example Domain"
    assert calls == [{
        "task": "Open https://example.com and report the page title",
        "api_key": "bb_secret",
        "project_id": "bb_project",
        "model_name": "google/gemini-2.5-flash",
        "max_steps": 3,
        "max_duration_sec": 30,
    }]

    ledger = CostLedger(tmp_path / ".local" / "state" / "browser-fetch-router" / "cost.db")
    assert ledger.session_total("bfr-browserbase-ok") == pytest.approx(0.25)


def test_browserbase_default_daily_cap_allows_fresh_session_after_prior_cost(
    tmp_path, monkeypatch
):
    from browser_fetch_router import interactive
    from browser_fetch_router.cost import CostLedger

    calls = []

    def fake_run_task(**kwargs):
        calls.append(kwargs)
        return {
            "status": "ok",
            "provider": "browserbase",
            "content_markdown": "Page title: Example Domain",
            "evidence": {
                "provider": "browserbase",
                "session_id": f"bb-{len(calls)}",
            },
        }

    fake_module = types.SimpleNamespace(run_task=fake_run_task)
    monkeypatch.setitem(
        sys.modules,
        "browser_fetch_router.providers.browserbase_stagehand",
        fake_module,
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BROWSERBASE_API_KEY", "bb_secret")

    monkeypatch.setenv("BFR_SESSION_ID", "bfr-browserbase-day-a")
    first = interactive.run_interactive_browser(
        "Open https://example.com and report the page title",
        provider="browserbase",
        allow_hosted_browser=True,
        max_cost_usd=0.25,
    )

    monkeypatch.setenv("BFR_SESSION_ID", "bfr-browserbase-day-b")
    second = interactive.run_interactive_browser(
        "Open https://example.com and report the page title",
        provider="browserbase",
        allow_hosted_browser=True,
        max_cost_usd=0.25,
    )

    assert first["status"] == "ok"
    assert second["status"] == "ok"
    assert len(calls) == 2

    ledger = CostLedger(tmp_path / ".local" / "state" / "browser-fetch-router" / "cost.db")
    assert ledger.session_total("bfr-browserbase-day-a") == pytest.approx(0.25)
    assert ledger.session_total("bfr-browserbase-day-b") == pytest.approx(0.25)


def test_browserbase_unreported_cost_counts_against_daily_cap(tmp_path, monkeypatch):
    from browser_fetch_router import interactive
    from browser_fetch_router.cost import CostLedger

    calls = []

    def fake_run_task(**kwargs):
        calls.append(kwargs)
        return {
            "status": "ok",
            "provider": "browserbase",
            "content_markdown": "Page title: Example Domain",
            "evidence": {
                "provider": "browserbase",
                "session_id": f"bb-{len(calls)}",
                "usage": {"input_tokens": 10, "output_tokens": 3},
            },
        }

    fake_module = types.SimpleNamespace(run_task=fake_run_task)
    monkeypatch.setitem(
        sys.modules,
        "browser_fetch_router.providers.browserbase_stagehand",
        fake_module,
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BROWSERBASE_API_KEY", "bb_secret")
    monkeypatch.setenv("BFR_HOSTED_BROWSER_DAILY_COST_CAP_USD", "0.30")

    monkeypatch.setenv("BFR_SESSION_ID", "bfr-browserbase-capped-a")
    first = interactive.run_interactive_browser(
        "Open https://example.com and report the page title",
        provider="browserbase",
        allow_hosted_browser=True,
        max_cost_usd=0.25,
    )

    monkeypatch.setenv("BFR_SESSION_ID", "bfr-browserbase-capped-b")
    second = interactive.run_interactive_browser(
        "Open https://example.com and report the page title",
        provider="browserbase",
        allow_hosted_browser=True,
        max_cost_usd=0.25,
    )

    assert first["status"] == "ok"
    assert second["status"] == "cost_cap_exceeded"
    assert second["evidence"]["reason"] == "paid_session_disabled_or_cap_exceeded"
    assert len(calls) == 1

    ledger = CostLedger(tmp_path / ".local" / "state" / "browser-fetch-router" / "cost.db")
    assert ledger.session_total("bfr-browserbase-capped-a") == pytest.approx(0.25)
    assert ledger.session_total("bfr-browserbase-capped-b") == pytest.approx(0.0)
    assert ledger.daily_total() == pytest.approx(0.25)


def test_browserbase_invalid_daily_cap_rejects_before_provider(tmp_path, monkeypatch):
    from browser_fetch_router import interactive

    calls = []

    def fake_run_task(**kwargs):
        calls.append(kwargs)
        return {
            "status": "ok",
            "provider": "browserbase",
            "content_markdown": "Page title: Example Domain",
            "evidence": {"provider": "browserbase", "session_id": "bb-invalid"},
        }

    fake_module = types.SimpleNamespace(run_task=fake_run_task)
    monkeypatch.setitem(
        sys.modules,
        "browser_fetch_router.providers.browserbase_stagehand",
        fake_module,
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BFR_SESSION_ID", "bfr-browserbase-invalid-daily")
    monkeypatch.setenv("BROWSERBASE_API_KEY", "bb_secret")

    for value in ("abc", "-1", "nan", "inf"):
        monkeypatch.setenv("BFR_HOSTED_BROWSER_DAILY_COST_CAP_USD", value)
        result = interactive.run_interactive_browser(
            "Open https://example.com and report the page title",
            provider="browserbase",
            allow_hosted_browser=True,
            max_cost_usd=0.25,
        )

        assert result["status"] == "usage_error"
        assert result["error"] == {
            "code": "invalid_daily_cost_cap",
            "env": "BFR_HOSTED_BROWSER_DAILY_COST_CAP_USD",
        }

    assert calls == []


def test_browserbase_daily_cap_below_call_cap_blocks_before_provider(tmp_path, monkeypatch):
    from browser_fetch_router import interactive
    from browser_fetch_router.cost import CostLedger

    calls = []

    def fake_run_task(**kwargs):
        calls.append(kwargs)
        return {
            "status": "ok",
            "provider": "browserbase",
            "content_markdown": "Page title: Example Domain",
            "evidence": {"provider": "browserbase", "session_id": "bb-low-cap"},
        }

    fake_module = types.SimpleNamespace(run_task=fake_run_task)
    monkeypatch.setitem(
        sys.modules,
        "browser_fetch_router.providers.browserbase_stagehand",
        fake_module,
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BFR_SESSION_ID", "bfr-browserbase-daily-below-call")
    monkeypatch.setenv("BROWSERBASE_API_KEY", "bb_secret")
    monkeypatch.setenv("BFR_HOSTED_BROWSER_DAILY_COST_CAP_USD", "0.10")

    result = interactive.run_interactive_browser(
        "Open https://example.com and report the page title",
        provider="browserbase",
        allow_hosted_browser=True,
        max_cost_usd=0.25,
    )

    assert result["status"] == "cost_cap_exceeded"
    assert result["evidence"]["reason"] == "paid_session_disabled_or_cap_exceeded"
    assert calls == []

    ledger = CostLedger(tmp_path / ".local" / "state" / "browser-fetch-router" / "cost.db")
    assert ledger.session_total("bfr-browserbase-daily-below-call") == pytest.approx(0.0)


def test_interactive_provider_capabilities_mark_cloud_and_browserbase_live_without_local():
    from browser_fetch_router import interactive

    capabilities = interactive.provider_capabilities()
    by_id = {item["id"]: item for item in capabilities}

    assert by_id["cloud"]["status"] == "live"
    assert by_id["cloud"]["requires_hosted_opt_in"] is True
    assert by_id["browserbase"]["status"] == "live"
    assert by_id["browserbase"]["requires_hosted_opt_in"] is True
    assert by_id["browserbase"]["requires"] == ["BROWSERBASE_API_KEY"]
    assert "local" not in by_id


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
    assert ledger.session_total("bfr-cloud-ok") == pytest.approx(0.18)


def test_cloud_reported_cost_settles_reservation_without_release_gap(
    tmp_path, monkeypatch
):
    from browser_fetch_router import interactive
    from browser_fetch_router.cost import CostLedger
    from browser_fetch_router.providers import browser_use_cloud

    def fake_run_task(**_kwargs):
        return {
            "status": "ok",
            "provider": "browser-use-cloud",
            "content_markdown": "Page title: Example Domain",
            "evidence": {
                "provider": "browser-use-cloud",
                "session_id": "remote-race",
                "remote_status": "stopped",
                "step_count": 1,
                "total_cost_usd": "0.18",
            },
        }

    original_release = CostLedger.release
    release_calls = []

    def racing_release(self, handle):
        released = original_release(self, handle)
        release_calls.append(handle)
        self.reserve(
            "bfr-cloud-race-other",
            "browser-use-cloud",
            0.20,
            request_cap=0.25,
            session_cap=0.25,
            daily_cap=0.30,
        )
        return released

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BFR_SESSION_ID", "bfr-cloud-race")
    monkeypatch.setenv("BFR_HOSTED_BROWSER_DAILY_COST_CAP_USD", "0.30")
    monkeypatch.setenv("BROWSER_USE_API_KEY", "bu_secret")
    monkeypatch.setattr(browser_use_cloud, "run_task", fake_run_task)
    monkeypatch.setattr(CostLedger, "release", racing_release)

    result = interactive.run_interactive_browser(
        "Open https://example.com and report the page title",
        provider="cloud",
        allow_hosted_browser=True,
        max_cost_usd=0.25,
    )

    assert result["status"] == "ok"
    assert result["content_markdown"] == "Page title: Example Domain"
    assert release_calls == []
    ledger = CostLedger(tmp_path / ".local" / "state" / "browser-fetch-router" / "cost.db")
    assert ledger.session_total("bfr-cloud-race") == pytest.approx(0.18)
    assert ledger.session_total("bfr-cloud-race-other") == pytest.approx(0.0)


def test_cloud_respects_explicit_cost_cap_below_default(tmp_path, monkeypatch):
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
                "session_id": "remote-low-cap",
                "remote_status": "stopped",
                "step_count": 1,
                "total_cost_usd": "0.04",
            },
        }

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BFR_SESSION_ID", "bfr-cloud-low-cap")
    monkeypatch.setenv("BROWSER_USE_API_KEY", "bu_secret")
    monkeypatch.setattr(browser_use_cloud, "run_task", fake_run_task)

    result = interactive.run_interactive_browser(
        "Open https://example.com and report the page title",
        provider="cloud",
        allow_hosted_browser=True,
        max_steps=3,
        max_duration_sec=30,
        max_cost_usd=0.05,
    )

    assert result["status"] == "ok"
    assert calls[0]["max_cost_usd"] == pytest.approx(0.05)

    ledger = CostLedger(tmp_path / ".local" / "state" / "browser-fetch-router" / "cost.db")
    assert ledger.session_total("bfr-cloud-low-cap") == pytest.approx(0.04)


def test_cloud_success_without_reported_cost_releases_reservation(tmp_path, monkeypatch):
    from browser_fetch_router import interactive
    from browser_fetch_router.cost import CostLedger
    from browser_fetch_router.providers import browser_use_cloud

    def fake_run_task(**_kwargs):
        return {
            "status": "ok",
            "provider": "browser-use-cloud",
            "content_markdown": "Page title: Example Domain",
            "evidence": {
                "provider": "browser-use-cloud",
                "session_id": "remote-no-cost",
                "remote_status": "stopped",
                "step_count": 1,
            },
        }

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BFR_SESSION_ID", "bfr-cloud-no-cost")
    monkeypatch.setenv("BROWSER_USE_API_KEY", "bu_secret")
    monkeypatch.setattr(browser_use_cloud, "run_task", fake_run_task)

    result = interactive.run_interactive_browser(
        "Open https://example.com and report the page title",
        provider="cloud",
        allow_hosted_browser=True,
        max_cost_usd=0.25,
    )

    assert result["status"] == "ok"
    ledger = CostLedger(tmp_path / ".local" / "state" / "browser-fetch-router" / "cost.db")
    assert ledger.session_total("bfr-cloud-no-cost") == pytest.approx(0.0)


def test_cloud_failed_provider_without_reported_cost_releases_reservation(tmp_path, monkeypatch):
    from browser_fetch_router import interactive
    from browser_fetch_router.cost import CostLedger
    from browser_fetch_router.providers import browser_use_cloud

    def fake_run_task(**_kwargs):
        return {
            "status": "provider_unavailable",
            "provider": "browser-use-cloud",
            "error": {"code": "browser_use_cloud_poll_failed"},
            "evidence": {
                "provider": "browser-use-cloud",
                "session_id": "remote-no-cost-failure",
                "remote_status": "running",
                "step_count": 1,
            },
        }

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BFR_SESSION_ID", "bfr-cloud-no-cost-failure")
    monkeypatch.setenv("BROWSER_USE_API_KEY", "bu_secret")
    monkeypatch.setattr(browser_use_cloud, "run_task", fake_run_task)

    result = interactive.run_interactive_browser(
        "Open https://example.com and report the page title",
        provider="cloud",
        allow_hosted_browser=True,
        max_cost_usd=0.25,
    )

    assert result["status"] == "provider_unavailable"
    ledger = CostLedger(tmp_path / ".local" / "state" / "browser-fetch-router" / "cost.db")
    assert ledger.session_total("bfr-cloud-no-cost-failure") == pytest.approx(0.0)


def test_cloud_failed_provider_with_reported_cost_records_actual_cost(tmp_path, monkeypatch):
    from browser_fetch_router import interactive
    from browser_fetch_router.cost import CostLedger
    from browser_fetch_router.providers import browser_use_cloud

    def fake_run_task(**_kwargs):
        return {
            "status": "provider_unavailable",
            "provider": "browser-use-cloud",
            "error": {"code": "browser_use_cloud_max_steps_exceeded"},
            "evidence": {
                "provider": "browser-use-cloud",
                "session_id": "remote-step-cap",
                "remote_status": "stopped",
                "step_count": 3,
                "total_cost_usd": "0.07",
                "max_steps": 3,
            },
        }

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BFR_SESSION_ID", "bfr-cloud-step-cap")
    monkeypatch.setenv("BROWSER_USE_API_KEY", "bu_secret")
    monkeypatch.setattr(browser_use_cloud, "run_task", fake_run_task)

    result = interactive.run_interactive_browser(
        "Open https://example.com and report the page title",
        provider="cloud",
        allow_hosted_browser=True,
        max_cost_usd=0.25,
    )

    assert result["status"] == "provider_unavailable"
    ledger = CostLedger(tmp_path / ".local" / "state" / "browser-fetch-router" / "cost.db")
    assert ledger.session_total("bfr-cloud-step-cap") == pytest.approx(0.07)


def test_cloud_provider_exception_releases_reservation(tmp_path, monkeypatch):
    from browser_fetch_router import interactive
    from browser_fetch_router.cost import CostLedger
    from browser_fetch_router.providers import browser_use_cloud

    def fake_run_task(**_kwargs):
        raise RuntimeError("provider transport crashed")

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BFR_SESSION_ID", "bfr-cloud-exception")
    monkeypatch.setenv("BROWSER_USE_API_KEY", "bu_secret")
    monkeypatch.setattr(browser_use_cloud, "run_task", fake_run_task)

    result = interactive.run_interactive_browser(
        "Open https://example.com and report the page title",
        provider="cloud",
        allow_hosted_browser=True,
        max_cost_usd=0.25,
    )

    assert result["status"] == "provider_unavailable"
    assert result["error"]["code"] == "browser_use_cloud_exception"
    ledger = CostLedger(tmp_path / ".local" / "state" / "browser-fetch-router" / "cost.db")
    assert ledger.session_total("bfr-cloud-exception") == pytest.approx(0.0)


def test_cloud_provider_import_error_releases_reservation(tmp_path, monkeypatch):
    from browser_fetch_router import interactive
    from browser_fetch_router.cost import CostLedger

    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "browser_fetch_router.providers" and "browser_use_cloud" in fromlist:
            raise ImportError("browser use provider missing")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BFR_SESSION_ID", "bfr-cloud-import-error")
    monkeypatch.setenv("BROWSER_USE_API_KEY", "bu_secret")
    monkeypatch.setattr(builtins, "__import__", fake_import)

    result = interactive.run_interactive_browser(
        "Open https://example.com and report the page title",
        provider="cloud",
        allow_hosted_browser=True,
        max_cost_usd=0.25,
    )

    assert result["status"] == "provider_unavailable"
    assert result["error"]["code"] == "browser_use_cloud_exception"
    ledger = CostLedger(tmp_path / ".local" / "state" / "browser-fetch-router" / "cost.db")
    assert ledger.session_total("bfr-cloud-import-error") == pytest.approx(0.0)


def test_cloud_session_cap_blocks_second_call_before_provider(tmp_path, monkeypatch):
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
                "session_id": f"remote-{len(calls)}",
                "remote_status": "stopped",
                "step_count": 1,
                "total_cost_usd": "0.18",
            },
        }

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BFR_SESSION_ID", "bfr-cloud-cumulative")
    monkeypatch.setenv("BROWSER_USE_API_KEY", "bu_secret")
    monkeypatch.setattr(browser_use_cloud, "run_task", fake_run_task)

    first = interactive.run_interactive_browser(
        "Open https://example.com and report the page title",
        provider="cloud",
        allow_hosted_browser=True,
        max_cost_usd=0.25,
    )
    second = interactive.run_interactive_browser(
        "Open https://example.com and report the page title",
        provider="cloud",
        allow_hosted_browser=True,
        max_cost_usd=0.25,
    )

    assert first["status"] == "ok"
    assert second["status"] == "cost_cap_exceeded"
    assert second["evidence"]["reason"] == "paid_session_disabled_or_cap_exceeded"
    assert len(calls) == 1

    ledger = CostLedger(tmp_path / ".local" / "state" / "browser-fetch-router" / "cost.db")
    assert ledger.session_total("bfr-cloud-cumulative") == pytest.approx(0.18)
    assert ledger.is_paid_disabled("bfr-cloud-cumulative")


def test_cloud_default_daily_cap_allows_fresh_session_after_prior_cost(tmp_path, monkeypatch):
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
                "session_id": f"remote-{len(calls)}",
                "remote_status": "stopped",
                "step_count": 1,
                "total_cost_usd": "0.18",
            },
        }

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BROWSER_USE_API_KEY", "bu_secret")
    monkeypatch.setattr(browser_use_cloud, "run_task", fake_run_task)

    monkeypatch.setenv("BFR_SESSION_ID", "bfr-cloud-day-a")
    first = interactive.run_interactive_browser(
        "Open https://example.com and report the page title",
        provider="cloud",
        allow_hosted_browser=True,
        max_cost_usd=0.25,
    )

    monkeypatch.setenv("BFR_SESSION_ID", "bfr-cloud-day-b")
    second = interactive.run_interactive_browser(
        "Open https://example.com and report the page title",
        provider="cloud",
        allow_hosted_browser=True,
        max_cost_usd=0.25,
    )

    assert first["status"] == "ok"
    assert second["status"] == "ok"
    assert len(calls) == 2

    ledger = CostLedger(tmp_path / ".local" / "state" / "browser-fetch-router" / "cost.db")
    assert ledger.session_total("bfr-cloud-day-a") == pytest.approx(0.18)
    assert ledger.session_total("bfr-cloud-day-b") == pytest.approx(0.18)


def test_cloud_configured_daily_cap_blocks_cross_session_call_before_provider(tmp_path, monkeypatch):
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
                "session_id": f"remote-{len(calls)}",
                "remote_status": "stopped",
                "step_count": 1,
                "total_cost_usd": "0.18",
            },
        }

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BROWSER_USE_API_KEY", "bu_secret")
    monkeypatch.setenv("BFR_HOSTED_BROWSER_DAILY_COST_CAP_USD", "0.25")
    monkeypatch.setattr(browser_use_cloud, "run_task", fake_run_task)

    monkeypatch.setenv("BFR_SESSION_ID", "bfr-cloud-day-capped-a")
    first = interactive.run_interactive_browser(
        "Open https://example.com and report the page title",
        provider="cloud",
        allow_hosted_browser=True,
        max_cost_usd=0.25,
    )

    monkeypatch.setenv("BFR_SESSION_ID", "bfr-cloud-day-capped-b")
    second = interactive.run_interactive_browser(
        "Open https://example.com and report the page title",
        provider="cloud",
        allow_hosted_browser=True,
        max_cost_usd=0.25,
    )

    assert first["status"] == "ok"
    assert second["status"] == "cost_cap_exceeded"
    assert second["evidence"]["reason"] == "paid_session_disabled_or_cap_exceeded"
    assert len(calls) == 1

    ledger = CostLedger(tmp_path / ".local" / "state" / "browser-fetch-router" / "cost.db")
    assert ledger.session_total("bfr-cloud-day-capped-a") == pytest.approx(0.18)
    assert ledger.session_total("bfr-cloud-day-capped-b") == pytest.approx(0.0)


def test_cloud_reported_cost_equal_to_cap_is_recorded(tmp_path, monkeypatch):
    from browser_fetch_router import interactive
    from browser_fetch_router.cost import CostLedger
    from browser_fetch_router.providers import browser_use_cloud

    def fake_run_task(**_kwargs):
        return {
            "status": "ok",
            "provider": "browser-use-cloud",
            "content_markdown": "Page title: Example Domain",
            "evidence": {
                "provider": "browser-use-cloud",
                "session_id": "remote-boundary",
                "remote_status": "stopped",
                "step_count": 1,
                "total_cost_usd": "0.25",
            },
        }

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BFR_SESSION_ID", "bfr-cloud-boundary")
    monkeypatch.setenv("BROWSER_USE_API_KEY", "bu_secret")
    monkeypatch.setattr(browser_use_cloud, "run_task", fake_run_task)

    result = interactive.run_interactive_browser(
        "Open https://example.com and report the page title",
        provider="cloud",
        allow_hosted_browser=True,
        max_cost_usd=0.25,
    )

    assert result["status"] == "ok"
    ledger = CostLedger(tmp_path / ".local" / "state" / "browser-fetch-router" / "cost.db")
    assert ledger.session_total("bfr-cloud-boundary") == pytest.approx(0.25)


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
    assert result["evidence"]["max_cost_usd"] == pytest.approx(0.25)
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
