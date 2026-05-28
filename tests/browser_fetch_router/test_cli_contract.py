import json
import os
import subprocess
import sys
from pathlib import Path

from browser_fetch_router.cli import normalize_argv

_REPO_ROOT = Path(__file__).resolve().parents[2]


def subprocess_env():
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return env


def run_cli(*args, env=None):
    return subprocess.run(
        [sys.executable, "-m", "browser_fetch_router", *args],
        env=env or subprocess_env(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def write_fake_global_bfr(tmp_path, schema_payload, *, doctor_status="ok"):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "browser-fetch-router"
    script = f"""#!/usr/bin/env python3
import json
import sys

schema_payload = {json.dumps(schema_payload)!r}
doctor_status = {doctor_status!r}

args = sys.argv[1:]
if args == ["--help"]:
    print("browser-fetch-router read-web read-user-tabs interactive-browser doctor cleanup schema install-agent")
    raise SystemExit(0)
if args == ["schema", "--json"]:
    print(schema_payload)
    raise SystemExit(0)
if args == ["doctor", "--json"]:
    print(json.dumps({{
        "schema_version": "browser-fetch-router.v1",
        "command": "doctor",
        "status": doctor_status,
        "url": None,
        "route": None,
        "provider": None,
        "title": None,
        "content_markdown": None,
        "artifacts": [],
        "quality": None,
        "evidence": {{"paths": {{}}}},
        "approval": {{"required": False, "scope": None}},
        "next_path": None,
        "error": None if doctor_status == "ok" else {{"code": "fake_doctor_failed"}},
    }}))
    raise SystemExit(0 if doctor_status == "ok" else 3)
print("unexpected args: " + " ".join(args), file=sys.stderr)
raise SystemExit(64)
"""
    exe.write_text(script)
    exe.chmod(0o755)
    return exe


def test_help_works_from_any_cwd(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "browser_fetch_router", "--help"],
        cwd=tmp_path,
        env=subprocess_env(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0
    assert "browser-fetch-router" in result.stdout


def test_schema_json_emits_schema_version():
    result = run_cli("schema", "--json")
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "browser-fetch-router.v1"
    assert payload["output_schema"]["$schema"] == "https://json-schema.org/draft/2020-12/schema"


def test_read_web_help_and_schema_document_public_and_paid_paths():
    from browser_fetch_router.schema import schema_payload

    result = run_cli("read-web", "--help")

    assert result.returncode == 0
    assert "public web" in result.stdout
    assert "Parallel" in result.stdout
    assert "--allow-paid" in result.stdout
    read_web_schema = schema_payload()["output_schema"]["commandFlags"]["read-web"]
    assert "public web" in read_web_schema["description"]
    assert "Parallel" in read_web_schema["properties"]["--allow-paid"]["description"]


def test_read_user_tabs_help_and_schema_include_cdp_setup_guidance():
    from browser_fetch_router.schema import schema_payload

    result = run_cli("read-user-tabs", "list", "--help")

    assert result.returncode == 0
    assert "127.0.0.1:9222" in result.stdout
    assert "--remote-debugging-port=9222" in result.stdout
    assert "--allow-remote-cdp" in result.stdout
    read_tabs_schema = schema_payload()["output_schema"]["commandFlags"]["read-user-tabs"]
    assert "127.0.0.1:9222" in read_tabs_schema["description"]
    assert "--user-data-dir=<temporary-profile>" in read_tabs_schema["description"]
    assert "--allow-remote-cdp" in read_tabs_schema["description"]


def test_interactive_browser_help_and_schema_mark_provider_capabilities():
    from browser_fetch_router.schema import schema_payload

    result = run_cli("interactive-browser", "--help")

    assert result.returncode == 0
    assert "cloud=live" in result.stdout
    assert "browserbase/local=unavailable" in result.stdout
    assert "stepCount" in result.stdout
    interactive_schema = schema_payload()["output_schema"]["commandFlags"]["interactive-browser"]
    assert "cloud=live" in interactive_schema["description"]
    assert "browserbase/local=unavailable" in interactive_schema["description"]
    capabilities = {item["id"]: item for item in interactive_schema["providerCapabilities"]}
    assert capabilities["cloud"]["status"] == "live"
    assert capabilities["browserbase"]["status"] == "unavailable"
    assert capabilities["local"]["status"] == "unavailable"


def test_invalid_flag_is_structured_usage_error():
    result = run_cli("read-web", "--invalid-flag", "--json")
    assert result.returncode == 64
    payload = json.loads(result.stdout)
    assert payload["status"] == "usage_error"
    assert payload["error"]["code"] == "usage_error"


def test_alias_invocation_prepends_subcommand():
    assert normalize_argv(
        None,
        invoked_as="/usr/local/bin/read-web",
        process_args=["https://example.com", "--json"],
    ) == ["read-web", "https://example.com", "--json"]


def test_primary_invocation_leaves_subcommand_args_unchanged():
    assert normalize_argv(
        None,
        invoked_as="/usr/local/bin/browser-fetch-router",
        process_args=["read-web", "https://example.com", "--json"],
    ) == ["read-web", "https://example.com", "--json"]


def test_doctor_uses_temp_home_and_reports_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    result = subprocess.run(
        [sys.executable, "-m", "browser_fetch_router", "doctor", "--json"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env={**__import__("os").environ, "HOME": str(tmp_path)},
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    paths = payload["evidence"]["paths"]
    assert str(tmp_path) in paths["config"]
    assert str(tmp_path) in paths["state"]
    assert str(tmp_path) in paths["cache"]


def test_doctor_global_install_verification_reports_current_shim(tmp_path):
    from browser_fetch_router.schema import schema_payload

    fake_exe = write_fake_global_bfr(tmp_path, schema_payload())
    env = subprocess_env()
    env["HOME"] = str(tmp_path / "home")
    env["PATH"] = str(fake_exe.parent) + os.pathsep + env.get("PATH", "")

    result = run_cli("doctor", "--global-install", "--json", env=env)

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    global_install = payload["evidence"]["global_install"]
    assert global_install["shim_path"] == str(fake_exe)
    assert global_install["schema_version"] == "browser-fetch-router.v1"
    assert global_install["schema_defaults"]["interactive-browser.--max-cost-usd"] == 0.25
    assert global_install["schema_defaults"]["interactive-browser.--max-steps"] == 10
    assert global_install["doctor_status"] == "ok"


def test_doctor_global_install_verification_detects_stale_schema_defaults(tmp_path):
    from browser_fetch_router.schema import schema_payload

    stale_schema = schema_payload()
    stale_schema["output_schema"]["commandFlags"]["interactive-browser"]["properties"][
        "--max-cost-usd"
    ]["default"] = 0.05
    fake_exe = write_fake_global_bfr(tmp_path, stale_schema)
    env = subprocess_env()
    env["HOME"] = str(tmp_path / "home")
    env["PATH"] = str(fake_exe.parent) + os.pathsep + env.get("PATH", "")

    result = run_cli("doctor", "--global-install", "--json", env=env)

    assert result.returncode == 3
    payload = json.loads(result.stdout)
    assert payload["status"] == "tool_setup_failed"
    assert payload["error"]["code"] == "stale_global_install"
    assert "pipx reinstall" in payload["error"]["reinstall_instruction"]
    global_install = payload["evidence"]["global_install"]
    assert global_install["schema_mismatches"] == [{
        "path": "interactive-browser.--max-cost-usd",
        "expected": 0.25,
        "actual": 0.05,
    }]


# --- Dispatcher safety net: every command must produce an envelope, never a
# Python traceback. The CLI's `_emit` wraps every handler so a bug in any
# handler (raised exception of any type) is converted to a structured payload
# with a documented exit code. -----------------------------------------------


def test_dispatcher_returns_unsafe_envelope_when_handler_raises_safety_error():
    """End-to-end through subprocess: read-web of a loopback URL exercises
    the full dispatch path. Confirms that a SafetyError raised deep inside
    the orchestration surfaces as `unsafe_url_blocked` (exit 4)."""
    result = run_cli("read-web", "http://127.0.0.1/", "--json")
    assert result.returncode == 4
    payload = json.loads(result.stdout)
    assert payload["status"] == "unsafe_url_blocked"
    assert payload["command"] == "read-web"
    assert payload["url"] == "http://127.0.0.1/"
    assert payload["error"]["code"]


def test_dispatcher_returns_internal_error_envelope_for_uncaught_exception(tmp_path, monkeypatch):
    """If a handler raises an exception OTHER than SafetyError, the
    dispatcher converts it to `internal_error` (exit 70) instead of letting
    a Python traceback escape to stderr. Verified by directly invoking
    `cli._emit` with a handler that raises an arbitrary exception."""
    from browser_fetch_router import cli

    def boom():
        raise RuntimeError("simulated bug")

    # Capture stdout to inspect the envelope.
    import io
    import contextlib

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        exit_code = cli._emit("read-web", url="https://example.com/", handler=boom)

    payload = json.loads(buf.getvalue())
    assert exit_code == 70
    assert payload["status"] == "internal_error"
    assert payload["command"] == "read-web"
    assert payload["url"] == "https://example.com/"
    assert payload["error"]["code"] == "uncaught_exception"
    assert payload["error"]["type"] == "RuntimeError"
    assert "simulated bug" in payload["error"]["message"]


def test_dispatcher_converts_keyboardinterrupt_to_interrupted_envelope(tmp_path, monkeypatch, capsys):
    """Round-3 fix (cluster 1, finding I): SIGINT during a long-running
    operation must produce an `interrupted` envelope + audit entry + exit
    130, NOT propagate as a bare KeyboardInterrupt traceback. Forensics
    requires the cancelled-but-already-acted case to be recorded — an
    attacker SIGINT-ing immediately after a side effect committed cannot
    suppress the audit trail.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    from browser_fetch_router import cli

    def interrupt():
        raise KeyboardInterrupt()

    exit_code = cli._emit("read-web", handler=interrupt, audit=False)
    out = capsys.readouterr().out
    import json

    payload = json.loads(out.strip().splitlines()[-1])
    assert payload["status"] == "interrupted"
    assert payload["error"]["code"] == "user_interrupt"
    assert exit_code == 130, "POSIX SIGINT exit convention"


def test_dispatcher_handler_returning_envelope_uses_status_exit_code():
    """When the handler returns a normal envelope, the dispatcher looks up
    the exit code from STATUS_EXIT_CODES rather than hardcoding 0/1."""
    from browser_fetch_router import cli
    from browser_fetch_router.schema import envelope

    def handler():
        return envelope(
            command="read-web",
            status="cost_cap_exceeded",  # exit code 5 per STATUS_EXIT_CODES
            url="https://example.com/",
            error={"code": "cost_cap_exceeded"},
        )

    import io
    import contextlib

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        exit_code = cli._emit("read-web", handler=handler)
    assert exit_code == 5


def test_interactive_browser_default_cost_cap_matches_schema():
    from browser_fetch_router import cli
    from browser_fetch_router.schema import schema_payload

    parser = cli.build_parser()
    args = parser.parse_args(["interactive-browser", "open page https://example.com"])
    interactive_schema = schema_payload()["output_schema"]["commandFlags"]["interactive-browser"]

    assert args.max_cost_usd == 0.25
    assert interactive_schema["properties"]["--max-cost-usd"]["default"] == 0.25
