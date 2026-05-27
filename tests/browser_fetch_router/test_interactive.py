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
