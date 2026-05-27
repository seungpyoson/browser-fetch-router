from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from browser_fetch_router.paths import (
    UnsafeDestination,
    atomic_write_bytes,
    home,
    validate_skill_md_dest,
)
from browser_fetch_router.schema import envelope

# Whitelist of env vars that may pass through to verification subprocesses.
# Anything else (including agent API keys) is dropped to avoid leaking
# credentials between agent contexts during install verification.
SAFE_ENV_KEYS = {
    "HOME",
    "PATH",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
    "USER",
    "LOGNAME",
    "BFR_SESSION_ID",
    "BFR_AGENT",
    "BFR_CDP_URL",
}

KIMI_INHERITANCE_WARNING = {
    "code": "kimi_brand_root_inheritance",
    "message": (
        "Writing Kimi's brand skill root can change Claude/Codex skill "
        "inheritance behavior."
    ),
}

KIMI_DEFAULT_SKIP_REASON = {
    "code": "default_disabled",
    "message": (
        "Kimi is supported only by explicit opt-in because its brand skill "
        "root can change Claude/Codex inheritance behavior."
    ),
}


@dataclass(frozen=True)
class AgentInstallContract:
    name: str
    root_parts: tuple[str, ...]
    env_var: str | None = None
    default_enabled: bool = True
    default_skip_reason: dict[str, str] | None = None
    explicit_warning: dict[str, str] | None = None
    create_root_on_explicit: bool = False

    def root(self) -> Path:
        if self.env_var and (override := os.environ.get(self.env_var)):
            return Path(override).expanduser()
        return home().joinpath(*self.root_parts)

    def destination(self) -> Path:
        return self.root() / "skills" / "browser-fetch-router" / "SKILL.md"


_AGENT_CONTRACTS = (
    AgentInstallContract("claude", (".claude",)),
    AgentInstallContract("codex", (".codex",), env_var="CODEX_HOME"),
    AgentInstallContract("gemini", (".gemini",), env_var="GEMINI_HOME"),
    AgentInstallContract(
        "kimi",
        (".kimi",),
        env_var="KIMI_HOME",
        default_enabled=False,
        default_skip_reason=KIMI_DEFAULT_SKIP_REASON,
        explicit_warning=KIMI_INHERITANCE_WARNING,
        create_root_on_explicit=True,
    ),
    AgentInstallContract(
        "opencode",
        (".config", "opencode"),
        env_var="OPENCODE_HOME",
    ),
    AgentInstallContract("pi", (".pi", "agent"), env_var="PI_HOME"),
)

INSTALL_CONTRACTS = {contract.name: contract for contract in _AGENT_CONTRACTS}
AGENTS = [contract.name for contract in _AGENT_CONTRACTS]


def _safe_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k in SAFE_ENV_KEYS}


def destination_for(agent: str, *, adapter_path: str | None = None) -> Path:
    if adapter_path:
        # Class-D round-17: agent-channel write containment. See W1/W5
        # in cli-write-containment-contract.md and the
        # `validate_skill_md_dest` docstring for the invariant. The
        # pre-existing is-directory check stays so the directory case
        # surfaces a more actionable error than the basename mismatch.
        path = Path(adapter_path).expanduser()
        if path.exists() and path.is_dir():
            raise ValueError(
                f"--adapter-path must be a file path, not a directory: {path}"
            )
        try:
            return validate_skill_md_dest(path)
        except UnsafeDestination as exc:
            raise ValueError(str(exc)) from exc
    return INSTALL_CONTRACTS[agent].destination()


def install_agents(
    agents: list[str],
    *,
    force: bool = False,
    default_mode: bool = False,
) -> dict[str, Any]:
    results = []
    all_ok = True
    for agent in agents:
        contract = INSTALL_CONTRACTS[agent]
        if default_mode and not contract.default_enabled:
            results.append({
                "agent": agent,
                "status": "skipped",
                "artifacts": [],
                "skip_reason": contract.default_skip_reason,
            })
            continue
        result = install_agent(agent, force=force)
        entry = {
            "agent": agent,
            "status": result.get("status"),
            "artifacts": result.get("artifacts") or [],
        }
        if result.get("error"):
            entry["error"] = result["error"]
        if result.get("evidence"):
            entry["evidence"] = result["evidence"]
        if result.get("warnings"):
            entry["warnings"] = result["warnings"]
        if result.get("status") not in {"ok", "skipped"}:
            all_ok = False
        results.append(entry)
    return envelope(
        command="install-agent",
        status="ok" if all_ok else "tool_setup_failed",
        artifacts=[
            artifact
            for entry in results
            for artifact in entry.get("artifacts", [])
        ],
        evidence={"results": results},
        results=results,
    )


def adapter_text(agent: str) -> str:
    """Return adapter SKILL.md content. Prefers packaged template; falls back
    to a generated default."""
    try:
        return resources.files("browser_fetch_router.adapters").joinpath(agent).joinpath("SKILL.md").read_text()
    except (FileNotFoundError, ModuleNotFoundError, IsADirectoryError):
        pass
    return _default_adapter_text(agent)


# Public alias for the spec name used in tests.
render_adapter = adapter_text


def verification_commands(agent: str) -> list[list[str]]:
    """Return the post-install verification commands the installer runs."""
    return [
        ["browser-fetch-router", "--help"],
        ["browser-fetch-router", "schema", "--json"],
        ["browser-fetch-router", "doctor", "--json"],
    ]


def _default_adapter_text(agent: str) -> str:
    return f"""---
name: browser-fetch-router
description: Thin adapter for browser-fetch-router CLI. Provider logic lives in the shared CLI.
---

# Browser Fetch Router

Use the shared `browser-fetch-router` CLI for web content access.

- Set `BFR_AGENT={agent}` and a UUID `BFR_SESSION_ID` for every agent task/run before invoking the CLI.
- Public URL: `browser-fetch-router read-web <url> --json`
- User tab list: `browser-fetch-router read-user-tabs list --json`
- User tab read: `browser-fetch-router read-user-tabs read active --json`
- Interactive browser task: `browser-fetch-router interactive-browser "<task>" --json`
- Do not reimplement provider selection, approvals, cache, cost controls, or cleanup in this adapter.

Adapter agent: {agent}
"""


def install_agent(agent: str, *, force: bool = False, adapter_path: str | None = None) -> dict[str, Any]:
    contract = INSTALL_CONTRACTS[agent]
    try:
        dest = destination_for(agent, adapter_path=adapter_path)
    except ValueError as exc:
        return envelope(
            command="install-agent",
            status="tool_setup_failed",
            error={"code": "invalid_adapter_path", "message": str(exc)},
        )
    warnings = []
    if not adapter_path and contract.explicit_warning:
        warnings.append(contract.explicit_warning)
    if not adapter_path and contract.create_root_on_explicit:
        dest.parent.parent.mkdir(parents=True, exist_ok=True)
    if not adapter_path and not dest.parent.parent.exists():
        return envelope(
            command="install-agent",
            status="tool_setup_failed",
            error={
                "code": "agent_adapter_path_unverified",
                "message": f"Default adapter directory {dest.parent.parent} not found. Provide --adapter-path if your agent uses a different location.",
            },
        )
    if dest.exists() and not force:
        return envelope(
            command="install-agent",
            status="tool_setup_failed",
            error={"code": "adapter_exists", "message": "Pass --force to overwrite"},
            artifacts=[{"path": str(dest)}],
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Route through atomic_write_bytes so a partial write (signal mid-
    # call, disk-full, permission edge) cannot leave a half-written
    # SKILL.md that the next agent invocation parses as truncated YAML
    # / markdown. Mode 0o644 keeps SKILL.md operator-readable — it is
    # not a credential file (the persistence-contract 0o600 default is
    # for internal state, not operator-facing config). Class fix r15-03
    # (no `path.write_text` / `path.write_bytes` in production code;
    # static guard locks the invariant in).
    atomic_write_bytes(dest, adapter_text(agent).encode("utf-8"), mode=0o644)
    verification = _run_verification()
    if not verification["success"]:
        return envelope(
            command="install-agent",
            status="tool_setup_failed",
            error={
                "code": "post_install_verification_failed",
                "details": verification["failures"],
            },
            artifacts=[{"path": str(dest)}, {"verification": verification}],
        )
    fields: dict[str, Any] = {
        "artifacts": [{"path": str(dest)}],
        "evidence": {"verification": verification},
    }
    if warnings:
        fields["warnings"] = warnings
    return envelope(command="install-agent", status="ok", **fields)


def _run_verification() -> dict[str, Any]:
    results: dict[str, Any] = {"success": True, "failures": []}
    env = _safe_env()
    py = sys.executable
    for cmd in [
        [py, "-m", "browser_fetch_router", "--help"],
        [py, "-m", "browser_fetch_router", "schema", "--json"],
        [py, "-m", "browser_fetch_router", "doctor", "--json"],
    ]:
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
                env=env,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            results["success"] = False
            results["failures"].append({"cmd": cmd, "reason": str(exc)})
            continue
        if result.returncode != 0:
            results["success"] = False
            results["failures"].append({"cmd": cmd, "returncode": result.returncode, "stderr": result.stderr[-500:]})
        elif "--json" in cmd and not result.stdout.strip().startswith("{"):
            results["success"] = False
            results["failures"].append({"cmd": cmd, "reason": "non_json_output"})
    return results
