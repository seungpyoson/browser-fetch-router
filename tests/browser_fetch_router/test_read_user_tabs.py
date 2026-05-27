from __future__ import annotations

import stat


def _patch_single_tab(monkeypatch, rut, *, tmp_path, url="https://news.ycombinator.com/", ws_url="ws://127.0.0.1:9222/devtools/page/T1"):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BFR_SESSION_ID", "sid-read-user-tabs")
    monkeypatch.setattr(rut, "cdp_base_url", lambda **_kw: "http://127.0.0.1:9222")
    monkeypatch.setattr(
        rut,
        "fetch_tab_list",
        lambda _base: [
            {
                "id": "T1",
                "title": "Readable",
                "url": url,
                "type": "page",
                "webSocketDebuggerUrl": ws_url,
            }
        ],
    )
    return url


def test_read_tab_success_passes_base_url_and_omits_raw_cdp(monkeypatch, tmp_path):
    from browser_fetch_router import cdp
    from browser_fetch_router import read_user_tabs as rut

    url = _patch_single_tab(monkeypatch, rut, tmp_path=tmp_path)

    def fake_fetch_tab_text(ws_url, *, base_url):
        assert ws_url == "ws://127.0.0.1:9222/devtools/page/T1"
        assert base_url == "http://127.0.0.1:9222"
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


def test_screenshot_tab_writes_approved_png_atomically(monkeypatch, tmp_path):
    from browser_fetch_router import cdp
    from browser_fetch_router import read_user_tabs as rut

    url = _patch_single_tab(monkeypatch, rut, tmp_path=tmp_path)
    output = tmp_path / "shot.png"

    monkeypatch.setattr(cdp, "fetch_tab_screenshot", lambda _base, _target: b"\x89PNG\r\n\x1a\nok")

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
