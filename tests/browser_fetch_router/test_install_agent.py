import json
import subprocess
from pathlib import Path

from browser_fetch_router.install_agent import adapter_text, destination_for


def test_claude_destination(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    dest = destination_for("claude")
    assert str(dest).endswith(".claude/skills/browser-fetch-router/SKILL.md")


def test_codex_destination_uses_codex_home(tmp_path, monkeypatch):
    codex_home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    assert destination_for("codex") == codex_home / "skills/browser-fetch-router/SKILL.md"


def test_pi_destination_uses_documented_agent_root(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("PI_HOME", raising=False)

    assert (
        destination_for("pi")
        == tmp_path / ".pi" / "agent" / "skills" / "browser-fetch-router" / "SKILL.md"
    )


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


def test_install_agent_all_skips_kimi_and_installs_documented_defaults(
    capsys, tmp_path, monkeypatch
):
    from browser_fetch_router import cli
    from browser_fetch_router import install_agent as module

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.delenv("GEMINI_HOME", raising=False)
    monkeypatch.delenv("KIMI_HOME", raising=False)
    monkeypatch.delenv("OPENCODE_HOME", raising=False)
    monkeypatch.delenv("PI_HOME", raising=False)
    for path in [
        tmp_path / ".claude" / "skills",
        tmp_path / ".codex" / "skills",
        tmp_path / ".gemini" / "skills",
        tmp_path / ".config" / "opencode" / "skills",
        tmp_path / ".pi" / "agent" / "skills",
    ]:
        path.mkdir(parents=True)
    monkeypatch.setattr(
        module,
        "_run_verification",
        lambda: {"success": True, "failures": []},
    )

    rc = cli.main(["install-agent", "--all", "--force", "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    assert [entry["agent"] for entry in payload["results"]] == [
        "claude",
        "codex",
        "gemini",
        "kimi",
        "opencode",
        "pi",
    ]
    kimi = payload["results"][3]
    assert kimi["status"] == "skipped"
    assert kimi["skip_reason"]["code"] == "default_disabled"
    assert kimi["artifacts"] == []
    assert (
        tmp_path / ".pi" / "agent" / "skills" / "browser-fetch-router" / "SKILL.md"
    ).exists()


def test_install_agent_all_fails_for_missing_default_root(
    capsys, tmp_path, monkeypatch
):
    from browser_fetch_router import cli
    from browser_fetch_router import install_agent as module
    from browser_fetch_router.status import STATUS_EXIT_CODES

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.delenv("GEMINI_HOME", raising=False)
    monkeypatch.delenv("KIMI_HOME", raising=False)
    monkeypatch.delenv("OPENCODE_HOME", raising=False)
    monkeypatch.delenv("PI_HOME", raising=False)
    for path in [
        tmp_path / ".claude" / "skills",
        tmp_path / ".gemini" / "skills",
        tmp_path / ".config" / "opencode" / "skills",
        tmp_path / ".pi" / "agent" / "skills",
    ]:
        path.mkdir(parents=True)
    monkeypatch.setattr(
        module,
        "_run_verification",
        lambda: {"success": True, "failures": []},
    )

    rc = cli.main(["install-agent", "--all", "--force", "--json"])

    assert rc == STATUS_EXIT_CODES["tool_setup_failed"]
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "tool_setup_failed"
    codex = next(entry for entry in payload["results"] if entry["agent"] == "codex")
    assert codex["error"]["code"] == "agent_adapter_path_unverified"
    assert "--adapter-path" in codex["error"]["message"]


def test_explicit_kimi_creates_root_and_warns(tmp_path, monkeypatch):
    from browser_fetch_router import install_agent as module

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("KIMI_HOME", raising=False)
    monkeypatch.setattr(
        module,
        "_run_verification",
        lambda: {"success": True, "failures": []},
    )

    result = module.install_agent("kimi", force=True)

    assert result["status"] == "ok"
    assert module.destination_for("kimi").exists()
    assert result["warnings"][0]["code"] == "kimi_brand_root_inheritance"


def test_select_kimi_installs_and_preserves_warning(tmp_path, monkeypatch):
    from browser_fetch_router import install_agent as module

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("KIMI_HOME", raising=False)
    monkeypatch.setattr(
        module,
        "_run_verification",
        lambda: {"success": True, "failures": []},
    )

    result = module.install_agents(["kimi"], force=True)

    assert result["status"] == "ok"
    assert result["results"][0]["status"] == "ok"
    assert result["results"][0]["warnings"][0]["code"] == (
        "kimi_brand_root_inheritance"
    )


def test_explicit_pi_cli_writes_documented_default(capsys, tmp_path, monkeypatch):
    from browser_fetch_router import cli
    from browser_fetch_router import install_agent as module

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("PI_HOME", raising=False)
    (tmp_path / ".pi" / "agent" / "skills").mkdir(parents=True)
    monkeypatch.setattr(
        module,
        "_run_verification",
        lambda: {"success": True, "failures": []},
    )

    rc = cli.main(["install-agent", "pi", "--force", "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    assert (
        tmp_path / ".pi" / "agent" / "skills" / "browser-fetch-router" / "SKILL.md"
    ).exists()


def test_explicit_kimi_cli_warns_without_preexisting_root(
    capsys, tmp_path, monkeypatch
):
    from browser_fetch_router import cli
    from browser_fetch_router import install_agent as module

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("KIMI_HOME", raising=False)
    monkeypatch.setattr(
        module,
        "_run_verification",
        lambda: {"success": True, "failures": []},
    )

    rc = cli.main(["install-agent", "kimi", "--force", "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    assert payload["warnings"][0]["code"] == "kimi_brand_root_inheritance"
    assert (
        tmp_path / ".kimi" / "skills" / "browser-fetch-router" / "SKILL.md"
    ).exists()


def test_env_overrides_write_under_root_containing_skills(tmp_path, monkeypatch):
    from browser_fetch_router import install_agent as module

    override_vars = {
        "CODEX_HOME": "codex-root",
        "GEMINI_HOME": "gemini-root",
        "KIMI_HOME": "kimi-root",
        "OPENCODE_HOME": "opencode-root",
        "PI_HOME": "pi-root",
    }
    for var, dirname in override_vars.items():
        root = tmp_path / dirname
        (root / "skills").mkdir(parents=True)
        monkeypatch.setenv(var, str(root))
    monkeypatch.setattr(
        module,
        "_run_verification",
        lambda: {"success": True, "failures": []},
    )

    result = module.install_agents(
        ["codex", "gemini", "opencode", "pi", "kimi"],
        force=True,
    )

    assert result["status"] == "ok"
    for agent in ["codex", "gemini", "opencode", "pi", "kimi"]:
        assert module.destination_for(agent).exists()


def test_select_force_overwrites_existing_adapter(tmp_path, monkeypatch):
    from browser_fetch_router import install_agent as module

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-root"))
    module.destination_for("codex").parent.parent.mkdir(parents=True)
    module.destination_for("codex").parent.mkdir(parents=True)
    module.destination_for("codex").write_text("old adapter", encoding="utf-8")
    monkeypatch.setattr(
        module,
        "_run_verification",
        lambda: {"success": True, "failures": []},
    )

    without_force = module.install_agents(["codex"], force=False)
    with_force = module.install_agents(["codex"], force=True)

    assert without_force["status"] == "tool_setup_failed"
    assert without_force["results"][0]["error"]["code"] == "adapter_exists"
    assert with_force["status"] == "ok"
    assert "old adapter" not in module.destination_for("codex").read_text(
        encoding="utf-8"
    )


def test_single_force_overwrites_existing_adapter(tmp_path, monkeypatch):
    from browser_fetch_router import install_agent as module

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GEMINI_HOME", str(tmp_path / "gemini-root"))
    module.destination_for("gemini").parent.parent.mkdir(parents=True)
    module.destination_for("gemini").parent.mkdir(parents=True)
    module.destination_for("gemini").write_text("old adapter", encoding="utf-8")
    monkeypatch.setattr(
        module,
        "_run_verification",
        lambda: {"success": True, "failures": []},
    )

    without_force = module.install_agent("gemini", force=False)
    with_force = module.install_agent("gemini", force=True)

    assert without_force["status"] == "tool_setup_failed"
    assert without_force["error"]["code"] == "adapter_exists"
    assert with_force["status"] == "ok"
    assert "old adapter" not in module.destination_for("gemini").read_text(
        encoding="utf-8"
    )


def test_install_agents_all_writes_every_default_destination(tmp_path, monkeypatch):
    from browser_fetch_router import install_agent as module

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))
    monkeypatch.setenv("GEMINI_HOME", str(tmp_path / ".gemini"))
    monkeypatch.setenv("KIMI_HOME", str(tmp_path / ".kimi"))
    monkeypatch.setenv("OPENCODE_HOME", str(tmp_path / ".config" / "opencode"))
    monkeypatch.setenv("PI_HOME", str(tmp_path / ".config" / "pi"))
    for agent in module.AGENTS:
        module.destination_for(agent).parent.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        module,
        "_run_verification",
        lambda: {"success": True, "failures": []},
    )

    result = module.install_agents(module.AGENTS, force=True)

    assert result["status"] == "ok"
    assert [entry["agent"] for entry in result["results"]] == module.AGENTS
    assert all(entry["status"] == "ok" for entry in result["results"])
    for agent in module.AGENTS:
        assert module.destination_for(agent).read_text(encoding="utf-8")


def test_install_agents_select_writes_subset_only(tmp_path, monkeypatch):
    from browser_fetch_router import install_agent as module

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))
    for agent in module.AGENTS:
        module.destination_for(agent).parent.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        module,
        "_run_verification",
        lambda: {"success": True, "failures": []},
    )

    result = module.install_agents(["claude", "codex"], force=True)

    assert result["status"] == "ok"
    assert [entry["agent"] for entry in result["results"]] == ["claude", "codex"]
    assert module.destination_for("claude").exists()
    assert module.destination_for("codex").exists()
    assert not module.destination_for("gemini").exists()


def test_install_agents_partial_failure_continues(tmp_path, monkeypatch):
    from browser_fetch_router import install_agent as module

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))
    module.destination_for("claude").parent.parent.mkdir(parents=True, exist_ok=True)

    result = module.install_agents(["claude", "codex"], force=True)

    assert result["status"] == "tool_setup_failed"
    assert [entry["agent"] for entry in result["results"]] == ["claude", "codex"]
    assert result["results"][0]["status"] == "ok"
    assert result["results"][1]["status"] == "tool_setup_failed"


def test_install_agent_all_rejects_adapter_path(capsys, tmp_path):
    from browser_fetch_router import cli
    from browser_fetch_router.status import STATUS_EXIT_CODES

    rc = None
    try:
        rc = cli.main([
            "install-agent",
            "--all",
            "--adapter-path",
            str(tmp_path / "SKILL.md"),
            "--json",
        ])
    except SystemExit as exc:
        rc = exc.code

    assert rc == STATUS_EXIT_CODES["usage_error"]
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "usage_error"
    assert payload["error"]["code"] == "usage_error"


def test_install_agent_select_cli_groups_requested_agents(capsys, monkeypatch):
    from browser_fetch_router import cli
    from browser_fetch_router.schema import envelope

    captured = {}

    def fake_install_agents(agents, *, force=False):
        captured["agents"] = agents
        captured["force"] = force
        return envelope(
            command="install-agent",
            status="ok",
            results=[{"agent": agent, "status": "ok"} for agent in agents],
        )

    monkeypatch.setattr(
        "browser_fetch_router.install_agent.install_agents",
        fake_install_agents,
    )

    rc = cli.main(["install-agent", "--select", "claude,codex", "--force", "--json"])

    assert rc == 0
    assert captured == {"agents": ["claude", "codex"], "force": True}
    payload = json.loads(capsys.readouterr().out)
    assert payload["results"] == [
        {"agent": "claude", "status": "ok"},
        {"agent": "codex", "status": "ok"},
    ]


def test_install_agent_schema_documents_multi_install_modes():
    from browser_fetch_router.schema import schema_payload

    install_schema = schema_payload()["output_schema"]["commandFlags"]["install-agent"]

    assert install_schema["properties"]["--all"]["type"] == "boolean"
    assert install_schema["properties"]["--select"]["type"] == "string"
    assert "required" not in install_schema
    assert {"required": ["agent"]} in install_schema["oneOf"]
    assert {"required": ["--all"]} in install_schema["oneOf"]
    assert {"required": ["--select"]} in install_schema["oneOf"]


def test_install_agent_schema_documents_default_and_supported_distinction():
    from browser_fetch_router.schema import schema_payload

    install_schema = schema_payload()["output_schema"]["commandFlags"]["install-agent"]

    assert "default agents" in install_schema["properties"]["--all"]["description"]
    assert "explicit-only" in install_schema["properties"]["--all"]["description"]
    assert "supported agents" in install_schema["properties"]["--select"]["description"]
    assert "SKILL.md" in install_schema["properties"]["--adapter-path"]["description"]


def test_install_agent_contract_docs_include_support_matrix_and_caveats():
    doc = Path("docs/browser-fetch-router-install-agent-contract.md").read_text(
        encoding="utf-8"
    )

    assert "Support Matrix" in doc
    assert "~/.pi/agent" in doc
    assert "Pi migration" in doc
    assert "Kimi" in doc
    assert "inheritance" in doc
    assert "--adapter-path" in doc
    assert "SKILL.md" in doc


def test_tracked_files_do_not_contain_contributor_local_paths():
    result = subprocess.run(
        ["git", "ls-files"],
        capture_output=True,
        text=True,
        check=True,
    )
    patterns = [
        "/" + "Users" + "/",
        "/" + "home" + "/",
        "C:" + "\\" + "Users" + "\\",
        "C:" + "/" + "Users" + "/",
        "%" + "USERPROFILE" + "%",
    ]
    offenders = []
    for relpath in result.stdout.splitlines():
        path = Path(relpath)
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if any(pattern in text for pattern in patterns):
            offenders.append(relpath)

    assert offenders == []
