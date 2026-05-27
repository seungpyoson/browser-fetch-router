from __future__ import annotations


def test_local_interactive_browser_reports_provider_unavailable(monkeypatch):
    from browser_fetch_router import interactive

    monkeypatch.setattr(interactive, "_local_browser_use_available", lambda: True)

    result = interactive.run_interactive_browser("read the current page", provider="local")

    assert result["status"] == "tool_setup_failed"
    assert result["error"]["code"] == "provider_unavailable"
    assert "launch_pending" not in result["error"].get("message", "")


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


def test_cloud_after_opt_in_reports_provider_unavailable(monkeypatch):
    from browser_fetch_router import interactive

    monkeypatch.setenv("BROWSER_USE_API_KEY", "present")

    result = interactive.run_interactive_browser(
        "read the current page",
        provider="cloud",
        allow_hosted_browser=True,
    )

    assert result["status"] == "tool_setup_failed"
    assert result["error"]["code"] == "provider_unavailable"
    assert result["evidence"]["provider"] == "cloud"
