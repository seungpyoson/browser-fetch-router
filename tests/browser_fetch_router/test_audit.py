"""Regression tests for audit-input sanitization."""
from browser_fetch_router.audit import sanitize_audit_input


def test_url_with_sensitive_query_keys_redacted():
    out = sanitize_audit_input("https://example.com/p?token=abc&id=1&signature=zzz")
    # Sensitive keys are redacted; non-sensitive (id) is preserved.
    assert "token=" in out and "abc" not in out
    assert "signature=" in out and "zzz" not in out
    assert "id=1" in out


def test_url_without_query_returns_unchanged():
    out = sanitize_audit_input("https://example.com/p")
    assert out == "https://example.com/p"


def test_natural_language_task_is_not_url_mangled():
    """Regression for Gemini #1.

    interactive-browser passes a free-form natural-language task to the
    audit log. urlsplit treats the first `?` as a query separator and
    urlunsplit then percent-encodes the rest of the sentence — corrupting
    the audit entry.

    Sanitizer must detect non-URL-shape (no scheme/netloc) and return
    secret-redacted text unchanged.
    """
    task = "buy ticket? please confirm and check status"
    out = sanitize_audit_input(task)
    assert out == task  # unchanged (no secrets, not URL-shaped)


def test_natural_language_task_with_secret_still_redacted():
    """Even non-URL-shaped input still gets secret-text redaction."""
    task = "delete the file using token sk-abcdef0123456789012345678901234567"
    out = sanitize_audit_input(task)
    assert "sk-abcdef" not in out
    assert "[redacted]" in out
    # Non-secret prefix preserved.
    assert "delete the file using token" in out


def test_url_with_only_path_question_mark_in_text_handled():
    """A genuine URL with sensitive query still gets redacted properly."""
    out = sanitize_audit_input(
        "https://example.com/api/items?token=secret&purchase=true"
    )
    assert "secret" not in out
    assert "token=" in out


def test_bare_question_mark_text_not_corrupted():
    """Free-form text with only `?` (no scheme) must not be URL-encoded."""
    text = "what about? this works fine"
    out = sanitize_audit_input(text)
    assert out == text


# --- External-review (Gemini round 2 #H1): CLI dispatcher emits audit
# for every command, not just read-web. Class fix moved audit out of
# per-handler call sites and into cli._emit so a new command cannot ship
# without auditing.


def _read_audit_log(home):
    import json
    from pathlib import Path

    path = Path(home) / ".local/state/browser-fetch-router/audit.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_cli_emit_writes_audit_for_read_web(tmp_path, monkeypatch, capsys):
    """read-web previously called append_audit itself. After centralization
    in cli._emit, the same audit entry must still appear with the same
    fields (command, status, input_url_or_task)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    from browser_fetch_router import cli

    # Force a guaranteed-blocked URL so we exercise the dispatcher's
    # SafetyError branch + audit on the blocked path.
    cli.main(["read-web", "http://169.254.169.254/"])
    capsys.readouterr()

    entries = _read_audit_log(tmp_path)
    assert entries, "no audit entries written"
    last = entries[-1]
    assert last["command"] == "read-web"
    assert last["status"] == "unsafe_url_blocked"
    assert last["input_url_or_task"] == "http://169.254.169.254/"


def test_cli_emit_writes_audit_for_interactive_browser(tmp_path, monkeypatch, capsys):
    """Regression for Gemini #H1: interactive-browser had NO append_audit
    call. With dispatcher-level audit, every invocation produces an
    audit entry, regardless of which command — the class fix."""
    monkeypatch.setenv("HOME", str(tmp_path))
    from browser_fetch_router import cli

    cli.main(["interactive-browser", "read the page"])
    capsys.readouterr()

    entries = _read_audit_log(tmp_path)
    commands = [e.get("command") for e in entries]
    assert "interactive-browser" in commands
    target = [e for e in entries if e["command"] == "interactive-browser"][-1]
    # input_url_or_task carries the task string for interactive-browser.
    assert target["input_url_or_task"] == "read the page"


def test_cli_emit_writes_audit_for_read_user_tabs(tmp_path, monkeypatch, capsys):
    """Regression for Gemini #H1: read-user-tabs had NO append_audit calls."""
    monkeypatch.setenv("HOME", str(tmp_path))
    from browser_fetch_router import cli

    # `revoke` is the easiest read-user-tabs subcommand to exercise without
    # CDP — touches the approval store and returns an envelope.
    cli.main(["read-user-tabs", "revoke", "hostname:example.com"])
    capsys.readouterr()

    entries = _read_audit_log(tmp_path)
    commands = [e.get("command") for e in entries]
    assert "read-user-tabs" in commands


def test_cli_emit_writes_audit_on_safety_error(tmp_path, monkeypatch, capsys):
    """Even when the handler raises SafetyError (URL blocked), the
    dispatcher must still emit an audit entry. Forensics requires logging
    the BLOCKED attempt — the most security-relevant entry of all."""
    monkeypatch.setenv("HOME", str(tmp_path))
    from browser_fetch_router import cli

    cli.main(["read-web", "http://localhost/secret"])
    capsys.readouterr()

    entries = _read_audit_log(tmp_path)
    assert entries, "blocked URL must still produce audit entry"
    assert entries[-1]["status"] == "unsafe_url_blocked"


def test_cli_emit_audit_disabled_for_schema(tmp_path, monkeypatch, capsys):
    """Counter-example: `schema` opts out of audit (audit=False). It is a
    pure metadata command — auditing it would be CI noise without forensic
    value. The opt-out is explicit per call site so future operational
    commands cannot accidentally inherit it."""
    monkeypatch.setenv("HOME", str(tmp_path))
    from browser_fetch_router import cli

    cli.main(["schema"])
    capsys.readouterr()

    entries = _read_audit_log(tmp_path)
    assert all(e.get("command") != "schema" for e in entries), entries
