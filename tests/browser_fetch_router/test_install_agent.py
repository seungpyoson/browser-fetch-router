from browser_fetch_router.install_agent import adapter_text, destination_for


def test_claude_destination(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    dest = destination_for("claude")
    assert str(dest).endswith(".claude/skills/browser-fetch-router/SKILL.md")


def test_codex_destination_uses_codex_home(tmp_path, monkeypatch):
    codex_home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    assert destination_for("codex") == codex_home / "skills/browser-fetch-router/SKILL.md"


def test_unknown_agent_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    import pytest

    with pytest.raises(KeyError):
        destination_for("unknown-agent")


def test_adapter_text_mentions_agent_name():
    text = adapter_text("kimi")
    assert "BFR_AGENT=kimi" in text
    assert "browser-fetch-router" in text


# --- Task 16: install verification ----------------------------------------


def test_adapter_template_mentions_only_shared_cli():
    from browser_fetch_router.install_agent import render_adapter

    body = render_adapter("codex")
    assert "browser-fetch-router read-web" in body
    # Adapter must not duplicate provider routing logic.
    assert "provider selection" not in body.lower()
    assert "implement provider" not in body.lower()


def test_install_plan_verifies_help_and_doctor():
    from browser_fetch_router.install_agent import verification_commands

    commands = verification_commands("codex")
    assert ["browser-fetch-router", "--help"] in commands
    assert ["browser-fetch-router", "doctor", "--json"] in commands
    assert ["browser-fetch-router", "schema", "--json"] in commands


def test_safe_env_for_subprocess_drops_agent_keys(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret-anthropic")
    monkeypatch.setenv("OPENAI_API_KEY", "secret-openai")
    monkeypatch.setenv("HOME", "/tmp/x")
    from browser_fetch_router.install_agent import _safe_env

    env = _safe_env()
    assert "ANTHROPIC_API_KEY" not in env
    assert "OPENAI_API_KEY" not in env
    assert env.get("HOME") == "/tmp/x"
