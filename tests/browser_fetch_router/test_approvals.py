from browser_fetch_router.approvals import approval_matches, normalize_scope


def test_exact_url_requires_query_match():
    assert approval_matches("exact:https://example.com/a?x=1", "https://example.com/a?x=1")
    assert not approval_matches("exact:https://example.com/a?x=1", "https://example.com/a?x=2")


def test_hostname_does_not_match_subdomain():
    assert approval_matches("hostname:example.com", "https://example.com/a")
    assert not approval_matches("hostname:example.com", "https://sub.example.com/a")


def test_wildcard_matches_child_subdomain():
    assert approval_matches("wildcard:example.com", "https://sub.example.com/a")
    assert approval_matches("wildcard:example.com", "https://example.com/a")


def test_normalize_scope_lowercases_and_idnas():
    # Mixed case → lowercase.
    assert normalize_scope("hostname:Example.COM") == "hostname:example.com"
    # Wildcard same.
    assert normalize_scope("wildcard:Example.COM") == "wildcard:example.com"
    # Exact URLs preserve case in path/query but host is lowered.
    norm = normalize_scope("exact:HTTPS://Example.COM/A?x=1")
    assert norm.startswith("exact:https://example.com/")


def test_unknown_scope_kind_is_invalid():
    """Round-9 r9-01: unknown scope kinds raise InvalidScope at
    canonicalization time so they cannot reach approval_matches.
    `approval_matches` defends against a stale stored-scope record by
    rejecting any kind not in VALID_SCOPE_KINDS, so a manually planted
    record stays harmless. Both behaviors locked in here."""
    import pytest

    from browser_fetch_router.approvals import (
        InvalidScope,
        approval_matches,
        normalize_scope,
    )

    with pytest.raises(InvalidScope):
        normalize_scope("invalid:foo")
    # Defense in depth: even if a malformed scope were planted directly
    # in the JSON store, approval_matches must reject it.
    assert not approval_matches("invalid:foo", "https://example.com/")


# --- Task 14: default-deny override + persistence --------------------------


def test_default_deny_overrides_wildcard():
    from browser_fetch_router.approvals import can_read_url

    allowed = can_read_url(
        "https://mail.google.com/mail/u/0",
        ["wildcard:google.com"],
        exact_one_time=[],
    )
    assert not allowed


def test_exact_one_time_overrides_default_deny():
    from browser_fetch_router.approvals import can_read_url

    allowed = can_read_url(
        "https://mail.google.com/mail/u/0",
        ["wildcard:google.com"],
        exact_one_time=["exact:https://mail.google.com/mail/u/0"],
    )
    assert allowed


def test_persistent_approval_survives_lookup(tmp_path, monkeypatch):
    from browser_fetch_router.approvals import (
        add_approval,
        list_active_scopes,
    )

    monkeypatch.setenv("HOME", str(tmp_path))
    add_approval("hostname:example.com", session_id="s1", persisted=True)
    scopes = list_active_scopes(session_id="s1")
    assert "hostname:example.com" in scopes


def test_session_approval_isolated_to_session(tmp_path, monkeypatch):
    from browser_fetch_router.approvals import add_approval, list_active_scopes

    monkeypatch.setenv("HOME", str(tmp_path))
    add_approval("hostname:example.com", session_id="s1", persisted=False)
    assert "hostname:example.com" in list_active_scopes(session_id="s1")
    assert "hostname:example.com" not in list_active_scopes(session_id="s2")


def test_revoke_removes_persistent_scope(tmp_path, monkeypatch):
    from browser_fetch_router.approvals import (
        add_approval,
        list_active_scopes,
        revoke_scope,
    )

    monkeypatch.setenv("HOME", str(tmp_path))
    add_approval("hostname:example.com", session_id="s1", persisted=True)
    result = revoke_scope("hostname:example.com")
    assert result["removed"] == 1
    assert "hostname:example.com" not in list_active_scopes(session_id="s1")


# --- External-review (Greptile #4): no ghost session approval for a
# default-denied URL. -------------------------------------------------------


def test_read_tab_does_not_write_session_approval_for_default_denied_url(tmp_path, monkeypatch):
    """Regression for Greptile #4. The non-persist branch of read_tab used
    to call add_approval unconditionally; for a default-denied URL like
    `mail.google.com`, that wrote a session-scoped record that could never
    grant access (can_read_url checks default-deny separately) but
    polluted the store. The unified guard `not is_default_denied(url)`
    now governs both persist and non-persist branches."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BFR_SESSION_ID", "sess-greptile-4")

    from browser_fetch_router import read_user_tabs as rut
    from browser_fetch_router.approvals import list_active_scopes

    denied_url = "https://mail.google.com/mail/u/0"

    monkeypatch.setattr(rut, "cdp_base_url", lambda **kw: "http://127.0.0.1:9222")
    monkeypatch.setattr(
        rut,
        "fetch_tab_list",
        lambda base: [
            {
                "id": "T1",
                "title": "Inbox",
                "url": denied_url,
                "type": "page",
                "webSocketDebuggerUrl": "ws://x",
            }
        ],
    )

    # Approval scope passed; persist=False; URL is default-denied.
    # The call exercises the post-permission code path (CDP extraction
    # returns tool_setup_failed because cdp.fetch_tab_text is stubbed).
    # That's fine — what matters for THIS regression is the approval-store
    # side effect.
    result = rut.read_tab(
        denied_url,
        approval_scope=f"exact:{denied_url}",
        persist_approval=False,
        session_id="sess-greptile-4",
    )
    assert result["status"] in {"tool_setup_failed", "approval_required"}, result

    # CRITICAL: no ghost record was written to the approval store. Pre-fix,
    # the elif branch unconditionally wrote a session-scoped record.
    scopes = list_active_scopes(session_id="sess-greptile-4")
    assert all("mail.google.com" not in s for s in scopes), scopes


def test_read_tab_does_write_session_approval_for_neutral_url(tmp_path, monkeypatch):
    """Counter-example: a neutral URL with persist_approval=False DOES
    write a session-scoped record (the unified guard only blocks the
    default-deny case)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BFR_SESSION_ID", "sess-neutral")

    from browser_fetch_router import read_user_tabs as rut
    from browser_fetch_router.approvals import list_active_scopes

    neutral_url = "https://news.ycombinator.com/"
    monkeypatch.setattr(rut, "cdp_base_url", lambda **kw: "http://127.0.0.1:9222")
    monkeypatch.setattr(
        rut,
        "fetch_tab_list",
        lambda base: [
            {
                "id": "T1",
                "title": "HN",
                "url": neutral_url,
                "type": "page",
                "webSocketDebuggerUrl": "ws://x",
            }
        ],
    )

    rut.read_tab(
        neutral_url,
        approval_scope=f"exact:{neutral_url}",
        persist_approval=False,
        session_id="sess-neutral",
    )
    scopes = list_active_scopes(session_id="sess-neutral")
    assert any("news.ycombinator.com" in s for s in scopes), scopes
