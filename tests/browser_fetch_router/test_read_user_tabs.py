from __future__ import annotations

import json
import stat
import sys
import types

import pytest


def _patch_single_tab(
    monkeypatch,
    rut,
    *,
    tmp_path,
    url="https://news.ycombinator.com/",
    ws_url="ws://127.0.0.1:9222/devtools/page/T1",
    tab_id="T1",
):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BFR_SESSION_ID", "sid-read-user-tabs")
    monkeypatch.setattr(rut, "cdp_base_url", lambda **_kw: "http://127.0.0.1:9222")
    monkeypatch.setattr(
        rut,
        "fetch_tab_list",
        lambda _base: [
            {
                "id": tab_id,
                "title": "Readable",
                "url": url,
                "type": "page",
                "webSocketDebuggerUrl": ws_url,
            }
        ],
    )
    return url


def test_setup_cdp_launch_starts_temp_loopback_chrome(monkeypatch, tmp_path):
    from browser_fetch_router import read_user_tabs as rut

    calls = []

    class FakePopen:
        pid = 4242

        def __init__(self, argv, **kwargs):
            calls.append((argv, kwargs))

    profile = tmp_path / "bfr-cdp-profile.test"
    monkeypatch.setattr(rut, "_find_chrome_executable", lambda: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    monkeypatch.setattr(rut.tempfile, "mkdtemp", lambda prefix: str(profile))
    monkeypatch.setattr(rut.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(rut, "_wait_for_cdp_ready", lambda _base: True, raising=False)
    monkeypatch.setattr(rut, "_register_launched_cdp_process", lambda **_kw: None)

    result = rut.setup_cdp(launch=True, start_url="https://example.com")

    assert result["status"] == "ok"
    assert result["evidence"]["cdp_base"] == "http://127.0.0.1:9222"
    assert result["evidence"]["pid"] == 4242
    assert result["evidence"]["profile_dir"] == str(profile)
    argv, kwargs = calls[0]
    assert argv[0] == "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    assert "--remote-debugging-address=127.0.0.1" in argv
    assert "--remote-debugging-port=9222" in argv
    assert f"--user-data-dir={profile}" in argv
    assert any(item.startswith("https://example.com") for item in argv)
    assert kwargs["start_new_session"] is True
    assert "normal profile" in result["evidence"]["setup"]["warning"]


def test_setup_cdp_launch_uses_lifecycle_profile_prefix(monkeypatch, tmp_path):
    from browser_fetch_router import lifecycle
    from browser_fetch_router import read_user_tabs as rut

    prefixes = []

    class FakePopen:
        pid = 4243

        def __init__(self, _argv, **_kwargs):
            self.argv = _argv
            self.kwargs = _kwargs

    profile = tmp_path / "custom-cdp.profile"
    monkeypatch.setattr(lifecycle, "CDP_PROFILE_PREFIX", "custom-cdp.")
    monkeypatch.setattr(rut, "_find_chrome_executable", lambda: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    monkeypatch.setattr(rut.tempfile, "mkdtemp", lambda prefix: prefixes.append(prefix) or str(profile))
    monkeypatch.setattr(rut.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(rut, "_wait_for_cdp_ready", lambda _base: True, raising=False)
    monkeypatch.setattr(rut, "_register_launched_cdp_process", lambda **_kw: None)

    result = rut.setup_cdp(launch=True, start_url="https://example.com")

    assert result["status"] == "ok"
    assert prefixes == ["custom-cdp."]


def test_setup_cdp_launch_registers_process_for_session_cleanup(monkeypatch, tmp_path):
    from browser_fetch_router import read_user_tabs as rut
    from browser_fetch_router.lifecycle import (
        CDP_PROCESS_KIND_READ_USER_TABS,
        session_registry_path,
    )

    class FakePopen:
        pid = 4244

        def __init__(self, _argv, **_kwargs):
            self.argv = _argv
            self.kwargs = _kwargs

    class FakeProcess:
        def __init__(self, pid):
            assert pid == 4244

        def create_time(self):
            return 123.456

    fake_psutil = types.SimpleNamespace(
        Process=FakeProcess,
        Error=Exception,
    )

    profile = tmp_path / "bfr-cdp-profile.cleanup"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BFR_SESSION_ID", "cdp-cleanup-session")
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)
    monkeypatch.setattr(rut, "_find_chrome_executable", lambda: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    monkeypatch.setattr(rut.tempfile, "mkdtemp", lambda prefix: str(profile))
    monkeypatch.setattr(rut.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(rut, "_wait_for_cdp_ready", lambda _base: True, raising=False)

    result = rut.setup_cdp(launch=True, start_url="https://example.com")

    path = session_registry_path("cdp-cleanup-session")
    assert result["status"] == "ok"
    assert result["evidence"]["session_id"] == "cdp-cleanup-session"
    assert path.exists()
    data = json.loads(path.read_text())
    process = data["local_processes"][0]
    assert process["pid"] == 4244
    assert process["create_time"] == pytest.approx(123.456)
    assert process["process_group"] == 4244
    assert process["info"]["kind"] == CDP_PROCESS_KIND_READ_USER_TABS
    assert process["info"]["profile_dir"] == str(profile)


def test_setup_cdp_launch_reports_registration_error(monkeypatch, tmp_path):
    from browser_fetch_router import read_user_tabs as rut

    class FakePopen:
        pid = 4245

        def __init__(self, _argv, **_kwargs):
            self.argv = _argv
            self.kwargs = _kwargs
            self.terminated = False

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            return 0

    class FakeProcess:
        def __init__(self, _pid):
            self.pid = _pid

        def create_time(self):
            raise OSError("registry unavailable")

    fake_psutil = types.SimpleNamespace(Process=FakeProcess)

    profile = tmp_path / "bfr-cdp-profile.registration-failed"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BFR_SESSION_ID", "cdp-registration-failure")
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)
    monkeypatch.setattr(rut, "_find_chrome_executable", lambda: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    monkeypatch.setattr(rut.tempfile, "mkdtemp", lambda prefix: str(profile))
    monkeypatch.setattr(rut.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(rut, "_wait_for_cdp_ready", lambda _base: True, raising=False)

    profile.mkdir()
    result = rut.setup_cdp(launch=True, start_url="https://example.com")

    assert result["status"] == "tool_setup_failed"
    assert result["error"]["code"] == "cdp_launch_registration_failed"
    assert result["evidence"]["registration_error"] == {
        "type": "OSError",
        "message": "registry unavailable",
    }
    assert not profile.exists()


def test_setup_cdp_launch_removes_profile_when_chrome_fails_to_start(
    monkeypatch, tmp_path
):
    from browser_fetch_router import read_user_tabs as rut

    profile = tmp_path / "bfr-cdp-profile.popen-failed"

    def fail_popen(*_args, **_kwargs):
        raise OSError("launch denied")

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BFR_SESSION_ID", "cdp-popen-failure")
    monkeypatch.setattr(rut, "_find_chrome_executable", lambda: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    monkeypatch.setattr(rut.tempfile, "mkdtemp", lambda prefix: str(profile))
    monkeypatch.setattr(rut.subprocess, "Popen", fail_popen)

    profile.mkdir()
    result = rut.setup_cdp(launch=True, start_url="https://example.com")

    assert result["status"] == "tool_setup_failed"
    assert result["error"]["code"] == "cdp_launch_failed"
    assert result["evidence"]["launch_error"] == {
        "type": "OSError",
        "message": "launch denied",
    }
    assert not profile.exists()


def test_setup_cdp_launch_preserves_safety_error_during_registration(
    monkeypatch, tmp_path
):
    from browser_fetch_router import read_user_tabs as rut
    from browser_fetch_router.url_safety import SafetyError

    class FakePopen:
        pid = 4246

        def __init__(self, _argv, **_kwargs):
            self.argv = _argv
            self.kwargs = _kwargs
            self.terminated = False

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            return 0

    class FakeProcess:
        def __init__(self, _pid):
            self.pid = _pid

        def create_time(self):
            raise SafetyError("blocked private address")

    fake_psutil = types.SimpleNamespace(Process=FakeProcess)

    profile = tmp_path / "bfr-cdp-profile.safety"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BFR_SESSION_ID", "cdp-safety")
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)
    monkeypatch.setattr(rut, "_find_chrome_executable", lambda: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    monkeypatch.setattr(rut.tempfile, "mkdtemp", lambda prefix: str(profile))
    monkeypatch.setattr(rut.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(rut, "_wait_for_cdp_ready", lambda _base: True, raising=False)

    with pytest.raises(SafetyError):
        rut.setup_cdp(launch=True, start_url="https://example.com")


def test_setup_cdp_launch_rejects_unsafe_start_url_before_chrome(monkeypatch):
    from browser_fetch_router import read_user_tabs as rut

    def fail_find_chrome():
        raise AssertionError("Chrome lookup should not run for an unsafe start URL")

    monkeypatch.setattr(rut, "_find_chrome_executable", fail_find_chrome)

    result = rut.setup_cdp(launch=True, start_url="file:///etc/passwd")

    assert result["status"] == "unsafe_url_blocked"
    assert result["error"]["code"] == "blocked_scheme"
    assert result["error"]["message"] == "URL blocked by safety policy"
    assert result["evidence"]["setup"]["cdp_base"] == "http://127.0.0.1:9222"


def test_find_chrome_executable_honors_chrome_bin(monkeypatch):
    from browser_fetch_router import read_user_tabs as rut

    monkeypatch.delenv("BFR_CHROME_PATH", raising=False)
    monkeypatch.delenv("CHROME_PATH", raising=False)
    monkeypatch.setenv("CHROME_BIN", "/custom/chrome")

    assert rut._find_chrome_executable() == "/custom/chrome"


def test_setup_cdp_launch_reports_failure_when_cdp_never_becomes_ready(monkeypatch, tmp_path):
    from browser_fetch_router import read_user_tabs as rut

    events = []

    class FakePopen:
        pid = 4243

        def __init__(self, argv, **kwargs):
            events.append(("launch", argv, kwargs))

        def terminate(self):
            events.append(("terminate",))

        def wait(self, timeout):
            events.append(("wait", timeout))

    profile = tmp_path / "bfr-cdp-profile.failed"
    profile.mkdir()
    monkeypatch.setattr(rut, "_find_chrome_executable", lambda: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    monkeypatch.setattr(rut.tempfile, "mkdtemp", lambda prefix: str(profile))
    monkeypatch.setattr(rut.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(rut, "_wait_for_cdp_ready", lambda _base: False, raising=False)

    result = rut.setup_cdp(launch=True, start_url="https://example.com")

    assert result["status"] == "tool_setup_failed"
    assert result["error"]["code"] == "cdp_launch_failed"
    assert result["evidence"]["pid"] == 4243
    assert ("terminate",) in events
    assert not profile.exists()


def test_list_tabs_maps_unexpected_redirect_to_cdp_specific_code(monkeypatch, tmp_path):
    from browser_fetch_router import cdp
    from browser_fetch_router import read_user_tabs as rut

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(rut, "cdp_base_url", lambda **_kw: "http://127.0.0.1:9222")

    def redirect(_base):
        raise cdp.CdpUnexpectedRedirect("unexpected_redirect:302")

    monkeypatch.setattr(rut, "fetch_tab_list", redirect)

    result = rut.list_tabs(session_id="sid-read-user-tabs")

    assert result["status"] == "tool_setup_failed"
    assert result["error"]["code"] == "cdp_unexpected_redirect"
    assert result["error"]["message"] == "CDP tab list endpoint returned an unexpected redirect."


def test_list_tabs_sanitizes_unknown_cdp_list_failure(monkeypatch, tmp_path):
    from browser_fetch_router import read_user_tabs as rut

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(rut, "cdp_base_url", lambda **_kw: "http://127.0.0.1:9222")

    def fail(_base):
        raise RuntimeError("cdp list failed; cookie=secret")

    monkeypatch.setattr(rut, "fetch_tab_list", fail)

    result = rut.list_tabs(session_id="sid-read-user-tabs")

    assert result["status"] == "tool_setup_failed"
    assert result["error"]["code"] == "cdp_unreachable"
    assert result["error"]["message"] == "CDP tab list endpoint was unreachable."
    assert "cookie=secret" not in result["error"]["message"]


def test_list_tabs_cdp_unreachable_includes_loopback_setup_guidance(monkeypatch, tmp_path):
    from browser_fetch_router import read_user_tabs as rut

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(rut, "cdp_base_url", lambda **_kw: "http://127.0.0.1:9222")

    def fail(_base):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(rut, "fetch_tab_list", fail)

    result = rut.list_tabs(session_id="sid-read-user-tabs")

    assert result["status"] == "tool_setup_failed"
    assert result["error"]["code"] == "cdp_unreachable"
    setup = result["evidence"]["setup"]
    assert setup["cdp_base"] == "http://127.0.0.1:9222"
    assert "--remote-debugging-address=127.0.0.1" in setup["required_flags"]
    assert "--remote-debugging-port=9222" in setup["required_flags"]
    assert "--user-data-dir=<temporary-profile>" in setup["required_flags"]
    assert "normal profile" in setup["warning"]


def test_read_tab_maps_unexpected_redirect_to_cdp_specific_code(monkeypatch, tmp_path):
    from browser_fetch_router import cdp
    from browser_fetch_router import read_user_tabs as rut

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BFR_SESSION_ID", "sid-read-user-tabs")
    monkeypatch.setattr(rut, "cdp_base_url", lambda **_kw: "http://127.0.0.1:9222")

    def redirect(_base):
        raise cdp.CdpUnexpectedRedirect("unexpected_redirect:302")

    monkeypatch.setattr(rut, "fetch_tab_list", redirect)

    result = rut.read_tab(
        "active",
        approval_scope="exact:https://news.ycombinator.com/",
        session_id="sid-read-user-tabs",
    )

    assert result["status"] == "tool_setup_failed"
    assert result["error"]["code"] == "cdp_unexpected_redirect"
    assert result["error"]["message"] == "CDP tab list endpoint returned an unexpected redirect."


def test_read_tab_maps_malformed_tab_list_json_to_specific_code(monkeypatch, tmp_path):
    from browser_fetch_router import cdp
    from browser_fetch_router import read_user_tabs as rut

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BFR_SESSION_ID", "sid-read-user-tabs")
    monkeypatch.setattr(rut, "cdp_base_url", lambda **_kw: "http://127.0.0.1:9222")

    def malformed_json(_base):
        raise cdp.CdpTabListMalformedJson("cdp_tab_list_malformed_json")

    monkeypatch.setattr(rut, "fetch_tab_list", malformed_json)

    result = rut.read_tab(
        "active",
        approval_scope="exact:https://news.ycombinator.com/",
        session_id="sid-read-user-tabs",
    )

    assert result["status"] == "tool_setup_failed"
    assert result["error"]["code"] == "cdp_tab_list_malformed_json"
    assert result["error"]["message"] == "CDP tab list endpoint returned malformed JSON."


def test_read_tab_success_passes_base_url_and_omits_raw_cdp(monkeypatch, tmp_path):
    from browser_fetch_router import cdp
    from browser_fetch_router import read_user_tabs as rut

    url = _patch_single_tab(monkeypatch, rut, tmp_path=tmp_path)

    def fake_fetch_tab_text(ws_url, *, base_url, authorize_url):
        assert ws_url == "ws://127.0.0.1:9222/devtools/page/T1"
        assert base_url is None
        assert authorize_url(url) is True
        return {"text": "abcdef", "isolated_world": True, "raw": {"secret": "nope"}}

    monkeypatch.setattr(cdp, "fetch_tab_text", fake_fetch_tab_text)

    result = rut.read_tab(
        url,
        approval_scope=f"exact:{url}",
        max_chars=3,
        session_id="sid-read-user-tabs",
    )

    assert result["status"] == "ok"
    assert result["content_markdown"] == "abc\n\n[TRUNCATED after 3 chars]"
    assert result["evidence"] == {"cdp_isolated_world": True, "tab_id": "T1"}
    assert "raw" not in result["evidence"]


def test_read_tab_rejects_mismatched_websocket_url_before_connect(monkeypatch, tmp_path):
    from browser_fetch_router import cdp
    from browser_fetch_router import read_user_tabs as rut

    url = _patch_single_tab(
        monkeypatch,
        rut,
        tmp_path=tmp_path,
        ws_url="ws://example.com:9222/devtools/page/T1",
    )
    called = {"connect": False}

    def fake_fetch_tab_text(*_a, **_kw):
        called["connect"] = True
        raise AssertionError("must not connect to mismatched WebSocket URL")

    monkeypatch.setattr(cdp, "fetch_tab_text", fake_fetch_tab_text)

    result = rut.read_tab(url, approval_scope=f"exact:{url}", session_id="sid-read-user-tabs")

    assert result["status"] == "tool_setup_failed"
    assert result["error"]["code"] == "cdp_websocket_url_mismatch"
    assert called["connect"] is False


def test_read_tab_maps_unreachable_websocket_without_leaking_details(monkeypatch, tmp_path):
    from browser_fetch_router import cdp
    from browser_fetch_router import read_user_tabs as rut

    url = _patch_single_tab(monkeypatch, rut, tmp_path=tmp_path)

    def fake_fetch_tab_text(*_a, **_kw):
        raise cdp.CdpWebSocketUnavailable("socket refused; cookie=secret")

    monkeypatch.setattr(cdp, "fetch_tab_text", fake_fetch_tab_text)

    result = rut.read_tab(url, approval_scope=f"exact:{url}", session_id="sid-read-user-tabs")

    assert result["status"] == "tool_setup_failed"
    assert result["error"]["code"] == "cdp_unreachable"
    assert "cookie=secret" not in result["error"].get("message", "")


def test_read_tab_maps_real_websocket_connect_failure_without_leaking_details(monkeypatch, tmp_path):
    from browser_fetch_router import read_user_tabs as rut
    import websockets.sync.client as sync_client

    url = _patch_single_tab(monkeypatch, rut, tmp_path=tmp_path)

    def fail_connect(*_args, **_kwargs):
        raise OSError("socket refused; cookie=secret")

    monkeypatch.setattr(sync_client, "connect", fail_connect)

    result = rut.read_tab(url, approval_scope=f"exact:{url}", session_id="sid-read-user-tabs")

    assert result["status"] == "tool_setup_failed"
    assert result["error"]["code"] == "cdp_unreachable"
    assert "cookie=secret" not in result["error"].get("message", "")


def test_read_tab_maps_unexpected_cdp_exception_without_traceback(monkeypatch, tmp_path):
    from browser_fetch_router import cdp
    from browser_fetch_router import read_user_tabs as rut

    url = _patch_single_tab(monkeypatch, rut, tmp_path=tmp_path)

    def fake_fetch_tab_text(*_a, **_kw):
        raise OSError("socket refused; cookie=secret")

    monkeypatch.setattr(cdp, "fetch_tab_text", fake_fetch_tab_text)

    result = rut.read_tab(url, approval_scope=f"exact:{url}", session_id="sid-read-user-tabs")

    assert result["status"] == "tool_setup_failed"
    assert result["error"]["code"] == "cdp_text_extraction_failed"
    assert result["error"]["message"] == "CDP operation failed."
    assert "cookie=secret" not in result["error"].get("message", "")


def test_read_tab_maps_missing_websocket_dependency(monkeypatch, tmp_path):
    from browser_fetch_router import cdp
    from browser_fetch_router import read_user_tabs as rut

    url = _patch_single_tab(monkeypatch, rut, tmp_path=tmp_path)

    def fake_fetch_tab_text(*_a, **_kw):
        raise cdp.CdpWebSocketDependencyMissing("websockets dependency is not installed")

    monkeypatch.setattr(cdp, "fetch_tab_text", fake_fetch_tab_text)

    result = rut.read_tab(url, approval_scope=f"exact:{url}", session_id="sid-read-user-tabs")

    assert result["status"] == "tool_setup_failed"
    assert result["error"]["code"] == "cdp_websocket_dependency_missing"
    assert "websockets" in result["error"].get("message", "")


def test_read_tab_maps_protocol_error_without_raw_cdp_payload(monkeypatch, tmp_path):
    from browser_fetch_router import cdp
    from browser_fetch_router import read_user_tabs as rut

    url = _patch_single_tab(monkeypatch, rut, tmp_path=tmp_path)

    def fake_fetch_tab_text(*_a, **_kw):
        raise cdp.CdpProtocolError('Runtime.evaluate failed {"cookie":"secret"}')

    monkeypatch.setattr(cdp, "fetch_tab_text", fake_fetch_tab_text)

    result = rut.read_tab(url, approval_scope=f"exact:{url}", session_id="sid-read-user-tabs")

    assert result["status"] == "tool_setup_failed"
    assert result["error"]["code"] == "cdp_text_extraction_failed"
    assert "cookie" not in result["error"].get("message", "")


def test_read_tab_rechecks_current_url_before_returning_text(monkeypatch, tmp_path):
    from browser_fetch_router import cdp
    from browser_fetch_router import read_user_tabs as rut

    url = _patch_single_tab(monkeypatch, rut, tmp_path=tmp_path)

    def fake_fetch_tab_text(ws_url, *, base_url, authorize_url):
        assert ws_url == "ws://127.0.0.1:9222/devtools/page/T1"
        assert base_url is None
        current_url = "https://mail.google.com/mail/u/0/#inbox"
        assert authorize_url(current_url) is False
        raise cdp.CdpAuthorizationError("cdp_current_url_not_authorized")

    monkeypatch.setattr(cdp, "fetch_tab_text", fake_fetch_tab_text)

    result = rut.read_tab(
        url,
        approval_scope=f"exact:{url}",
        session_id="sid-read-user-tabs",
    )

    assert result["status"] == "approval_required"
    assert result["error"]["code"] == "approval_required_for_current_tab"
    assert result.get("content_markdown") is None


def test_screenshot_tab_rejects_mismatched_websocket_url_before_capture(monkeypatch, tmp_path):
    from browser_fetch_router import cdp
    from browser_fetch_router import read_user_tabs as rut

    url = _patch_single_tab(
        monkeypatch,
        rut,
        tmp_path=tmp_path,
        ws_url="ws://example.com:9222/devtools/page/T1",
    )
    output = tmp_path / "shot.png"
    called = {"capture": False}

    def fake_screenshot(*_a, **_kw):
        called["capture"] = True
        return b"PNG"

    monkeypatch.setattr(cdp, "fetch_tab_screenshot", fake_screenshot)

    result = rut.screenshot_tab(
        url,
        output=output,
        approval_scope=f"exact:{url}",
        session_id="sid-read-user-tabs",
    )

    assert result["status"] == "tool_setup_failed"
    assert result["error"]["code"] == "cdp_websocket_url_mismatch"
    assert called["capture"] is False
    assert not output.exists()


def test_screenshot_tab_rejects_missing_websocket_url_before_capture(monkeypatch, tmp_path):
    from browser_fetch_router import cdp
    from browser_fetch_router import read_user_tabs as rut

    url = _patch_single_tab(monkeypatch, rut, tmp_path=tmp_path, ws_url=None)
    output = tmp_path / "shot.png"
    called = {"capture": False}

    def fake_screenshot(*_a, **_kw):
        called["capture"] = True
        return b"PNG"

    monkeypatch.setattr(cdp, "fetch_tab_screenshot", fake_screenshot)

    result = rut.screenshot_tab(
        url,
        output=output,
        approval_scope=f"exact:{url}",
        session_id="sid-read-user-tabs",
    )

    assert result["status"] == "tool_setup_failed"
    assert result["error"]["code"] == "cdp_websocket_url_invalid"
    assert called["capture"] is False
    assert not output.exists()


def test_screenshot_tab_writes_approved_png_atomically(monkeypatch, tmp_path):
    from browser_fetch_router import cdp
    from browser_fetch_router import read_user_tabs as rut

    url = _patch_single_tab(monkeypatch, rut, tmp_path=tmp_path)
    output = tmp_path / "shot.png"

    def fake_screenshot(_base, _target, *, authorize_url):
        assert authorize_url(url) is True
        return b"\x89PNG\r\n\x1a\nok"

    monkeypatch.setattr(cdp, "fetch_tab_screenshot", fake_screenshot)

    result = rut.screenshot_tab(
        url,
        output=output,
        approval_scope=f"exact:{url}",
        session_id="sid-read-user-tabs",
    )

    assert result["status"] == "ok"
    assert result["artifacts"] == [{"path": str(output), "kind": "image/png"}]
    assert output.read_bytes().startswith(b"\x89PNG")
    assert stat.S_IMODE(output.stat().st_mode) == 0o600


def test_screenshot_tab_rejects_missing_initial_tab_id_before_capture(monkeypatch, tmp_path):
    from browser_fetch_router import cdp
    from browser_fetch_router import read_user_tabs as rut

    url = _patch_single_tab(monkeypatch, rut, tmp_path=tmp_path, tab_id="")
    output = tmp_path / "shot.png"
    called = {"capture": False}

    def fake_screenshot(*_args, **_kwargs):
        called["capture"] = True
        return b"\x89PNG\r\n\x1a\nok"

    monkeypatch.setattr(cdp, "fetch_tab_screenshot", fake_screenshot)

    result = rut.screenshot_tab(
        url,
        output=output,
        approval_scope=f"exact:{url}",
        session_id="sid-read-user-tabs",
    )

    assert result["status"] == "tool_setup_failed"
    assert result["error"]["code"] == "cdp_tab_missing_id"
    assert result["error"]["message"] == "Resolved tab did not expose a CDP tab id."
    assert called["capture"] is False
    assert not output.exists()


def test_screenshot_tab_maps_relist_failure_without_writing_output(monkeypatch, tmp_path):
    from browser_fetch_router import cdp
    from browser_fetch_router import read_user_tabs as rut

    url = _patch_single_tab(monkeypatch, rut, tmp_path=tmp_path)
    output = tmp_path / "shot.png"

    def fail_list(*_args, **_kwargs):
        raise RuntimeError("cdp list failed; cookie=secret")

    monkeypatch.setattr(cdp, "fetch_tab_list", fail_list)

    result = rut.screenshot_tab(
        url,
        output=output,
        approval_scope=f"exact:{url}",
        session_id="sid-read-user-tabs",
    )

    assert result["status"] == "tool_setup_failed"
    assert result["error"]["code"] == "cdp_unreachable"
    assert "cookie=secret" not in result["error"].get("message", "")
    assert not output.exists()


def test_screenshot_tab_maps_unexpected_cdp_exception_without_writing_output(monkeypatch, tmp_path):
    from browser_fetch_router import cdp
    from browser_fetch_router import read_user_tabs as rut

    url = _patch_single_tab(monkeypatch, rut, tmp_path=tmp_path)
    output = tmp_path / "shot.png"

    def fake_screenshot(*_args, **_kwargs):
        raise OSError("cdp screenshot failed; cookie=secret")

    monkeypatch.setattr(cdp, "fetch_tab_screenshot", fake_screenshot)

    result = rut.screenshot_tab(
        url,
        output=output,
        approval_scope=f"exact:{url}",
        session_id="sid-read-user-tabs",
    )

    assert result["status"] == "tool_setup_failed"
    assert result["error"]["code"] == "cdp_screenshot_failed"
    assert result["error"]["message"] == "CDP operation failed."
    assert "cookie=secret" not in result["error"].get("message", "")
    assert not output.exists()


def test_screenshot_tab_rechecks_current_url_before_writing_output(monkeypatch, tmp_path):
    from browser_fetch_router import cdp
    from browser_fetch_router import read_user_tabs as rut

    url = _patch_single_tab(monkeypatch, rut, tmp_path=tmp_path)
    output = tmp_path / "shot.png"

    def fake_screenshot(_base, _target, *, authorize_url):
        current_url = "https://mail.google.com/mail/u/0/#inbox"
        assert authorize_url(current_url) is False
        raise cdp.CdpAuthorizationError("cdp_current_url_not_authorized")

    monkeypatch.setattr(cdp, "fetch_tab_screenshot", fake_screenshot)

    result = rut.screenshot_tab(
        url,
        output=output,
        approval_scope=f"exact:{url}",
        session_id="sid-read-user-tabs",
    )

    assert result["status"] == "approval_required"
    assert result["error"]["code"] == "approval_required_for_current_tab"
    assert not output.exists()
