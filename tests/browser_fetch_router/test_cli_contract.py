import json
import subprocess
import sys

from browser_fetch_router.cli import normalize_argv


def run_cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "browser_fetch_router", *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_help_works_from_any_cwd(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "browser_fetch_router", "--help"],
        cwd=tmp_path,
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
