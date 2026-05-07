import json
import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]


def subprocess_env(base=None):
    env = dict(base or os.environ)
    env["PYTHONPATH"] = str(_REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return env


def run_cli(*args, **kwargs):
    env = subprocess_env(kwargs.pop("env", None))
    return subprocess.run(
        [sys.executable, "-m", "browser_fetch_router", *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env=env,
        **kwargs,
    )


def test_acceptance_blocks_localhost():
    result = run_cli("read-web", "http://127.0.0.1:3000", "--json")
    assert result.returncode == 4
    assert json.loads(result.stdout)["status"] == "unsafe_url_blocked"


def test_acceptance_blocks_file_scheme():
    result = run_cli("read-web", "file:///etc/passwd", "--json")
    assert result.returncode == 4
    assert json.loads(result.stdout)["status"] == "unsafe_url_blocked"


def test_acceptance_blocks_aws_metadata():
    result = run_cli("read-web", "http://169.254.169.254/latest/meta-data/", "--json")
    assert result.returncode == 4
    assert json.loads(result.stdout)["status"] == "unsafe_url_blocked"


def test_acceptance_blocks_obfuscated_loopback():
    # Integer-encoded 127.0.0.1.
    result = run_cli("read-web", "http://2130706433/", "--json")
    assert result.returncode == 4
    assert json.loads(result.stdout)["status"] == "unsafe_url_blocked"


def test_acceptance_schema_command():
    result = run_cli("schema", "--json")
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "browser-fetch-router.v1"
    assert payload["output_schema"]["$schema"] == "https://json-schema.org/draft/2020-12/schema"


def test_acceptance_doctor_command(tmp_path):
    env = {**os.environ, "HOME": str(tmp_path)}
    result = run_cli("doctor", "--json", env=env)
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"


def test_acceptance_help_works_from_tmp(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "browser_fetch_router", "--help"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        cwd=tmp_path,
        env=subprocess_env(),
    )
    assert result.returncode == 0
    assert "browser-fetch-router" in result.stdout
