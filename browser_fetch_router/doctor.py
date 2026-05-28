from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Any

from browser_fetch_router.paths import cache_dir, config_dir, ensure_all_dirs, state_dir
from browser_fetch_router.schema import envelope, schema_payload

GLOBAL_INSTALL_REINSTALL_INSTRUCTION = (
    "Reinstall the reviewed branch into the global environment, for example "
    "`pipx reinstall --force .`, then rerun doctor --global-install."
)
SAFE_GLOBAL_ENV_KEYS = {
    "HOME",
    "PATH",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
    "USER",
    "LOGNAME",
}


def _too_permissive(path: Path) -> bool:
    if not path.exists():
        return False
    mode = stat.S_IMODE(path.stat().st_mode)
    return bool(mode & 0o077)


def run_doctor(*, global_install: bool = False) -> dict[str, object]:
    paths = ensure_all_dirs()
    checked = [
        state_dir(),
        config_dir(),
        state_dir() / "audit.jsonl",
        config_dir() / "approvals.json",
    ]
    bad = [str(path) for path in checked if _too_permissive(path)]
    if bad:
        return envelope(
            command="doctor",
            status="tool_setup_failed",
            error={"code": "insecure_permissions", "paths": bad},
            evidence={"paths": paths},
        )
    cache_warn: list[str] = []
    if cache_dir().exists():
        for entry in cache_dir().rglob("*"):
            if entry.is_file() and (stat.S_IMODE(entry.stat().st_mode) & 0o033):
                cache_warn.append(str(entry))
    evidence: dict[str, Any] = {"paths": paths}
    if global_install:
        global_result = verify_global_install()
        evidence["global_install"] = global_result["evidence"]
        if not global_result["ok"]:
            return envelope(
                command="doctor",
                status="tool_setup_failed",
                error={
                    "code": "stale_global_install",
                    "reinstall_instruction": GLOBAL_INSTALL_REINSTALL_INSTRUCTION,
                },
                evidence=evidence,
            )
    payload = envelope(command="doctor", status="ok", evidence=evidence)
    if cache_warn:
        payload.setdefault("warnings", []).append(
            {"code": "cache_too_permissive", "paths": cache_warn}
        )
    return payload


def verify_global_install(command: str = "browser-fetch-router") -> dict[str, Any]:
    shim = shutil.which(command)
    evidence: dict[str, Any] = {
        "command": command,
        "shim_path": shim,
        "symlink_target": None,
        "schema_version": None,
        "schema_defaults": {},
        "schema_mismatches": [],
        "command_mismatches": [],
        "doctor_status": None,
    }
    if not shim:
        evidence["command_mismatches"].append({
            "path": "command",
            "expected": command,
            "actual": None,
        })
        return {"ok": False, "evidence": evidence}
    shim_path = Path(shim)
    if shim_path.is_symlink():
        evidence["symlink_target"] = str(shim_path.resolve())

    expected = _expected_global_schema_contract()
    help_result = _run_global_command([shim, "--help"])
    if help_result["returncode"] != 0:
        evidence["command_mismatches"].append({
            "path": "--help",
            "expected": 0,
            "actual": help_result["returncode"],
        })
    else:
        help_text = help_result["stdout"]
        missing = [command for command in expected["commands"] if command not in help_text]
        if missing:
            evidence["command_mismatches"].append({
                "path": "--help.commands",
                "expected": expected["commands"],
                "actual_missing": missing,
            })

    schema_result = _run_global_command([shim, "schema", "--json"])
    if schema_result["returncode"] != 0:
        evidence["schema_mismatches"].append({
            "path": "schema",
            "expected": 0,
            "actual": schema_result["returncode"],
        })
    else:
        actual_schema = _load_json(schema_result["stdout"])
        if not actual_schema:
            evidence["schema_mismatches"].append({
                "path": "schema",
                "expected": "json",
                "actual": "non_json",
            })
        else:
            actual_contract = _global_schema_contract(actual_schema)
            evidence["schema_version"] = actual_contract["schema_version"]
            evidence["schema_defaults"] = actual_contract["schema_defaults"]
            evidence["schema_mismatches"].extend(
                _schema_mismatches(expected, actual_contract)
            )

    doctor_result = _run_global_command([shim, "doctor", "--json"])
    if doctor_result["returncode"] != 0:
        evidence["command_mismatches"].append({
            "path": "doctor",
            "expected": 0,
            "actual": doctor_result["returncode"],
        })
    doctor_payload = _load_json(doctor_result["stdout"])
    if doctor_payload:
        evidence["doctor_status"] = doctor_payload.get("status")
    if evidence["doctor_status"] != "ok":
        evidence["command_mismatches"].append({
            "path": "doctor.status",
            "expected": "ok",
            "actual": evidence["doctor_status"],
        })

    ok = not evidence["schema_mismatches"] and not evidence["command_mismatches"]
    return {"ok": ok, "evidence": evidence}


def _run_global_command(cmd: list[str]) -> dict[str, Any]:
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
            env={k: v for k, v in os.environ.items() if k in SAFE_GLOBAL_ENV_KEYS},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"returncode": None, "stdout": "", "stderr": str(exc)}
    return {
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def _load_json(text: str) -> dict[str, Any] | None:
    try:
        data = json.loads(text or "{}")
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _expected_global_schema_contract() -> dict[str, Any]:
    return _global_schema_contract(schema_payload())


def _global_schema_contract(payload: dict[str, Any]) -> dict[str, Any]:
    output_schema = payload.get("output_schema")
    if not isinstance(output_schema, dict):
        output_schema = {}
    command_flags = output_schema.get("commandFlags")
    if not isinstance(command_flags, dict):
        command_flags = {}
    interactive = command_flags.get("interactive-browser")
    if not isinstance(interactive, dict):
        interactive = {}
    properties = interactive.get("properties")
    if not isinstance(properties, dict):
        properties = {}
    provider_capabilities = interactive.get("providerCapabilities") or []
    provider_statuses = {
        item.get("id"): item.get("status")
        for item in provider_capabilities
        if isinstance(item, dict)
    }

    def default_for(flag: str) -> Any:
        flag_schema = properties.get(flag)
        return flag_schema.get("default") if isinstance(flag_schema, dict) else None

    return {
        "schema_version": payload.get("schema_version"),
        "commands": payload.get("commands") if isinstance(payload.get("commands"), list) else [],
        "schema_defaults": {
            "interactive-browser.--max-cost-usd": default_for("--max-cost-usd"),
            "interactive-browser.--max-steps": default_for("--max-steps"),
            "interactive-browser.provider.cloud.status": provider_statuses.get("cloud"),
            "interactive-browser.provider.browserbase.status": provider_statuses.get(
                "browserbase"
            ),
        },
    }


def _schema_mismatches(
    expected: dict[str, Any],
    actual: dict[str, Any],
) -> list[dict[str, Any]]:
    mismatches = []
    if actual["schema_version"] != expected["schema_version"]:
        mismatches.append({
            "path": "schema_version",
            "expected": expected["schema_version"],
            "actual": actual["schema_version"],
        })
    for path, expected_value in expected["schema_defaults"].items():
        actual_value = actual["schema_defaults"].get(path)
        if actual_value != expected_value:
            mismatches.append({
                "path": path,
                "expected": expected_value,
                "actual": actual_value,
            })
    return mismatches
