from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from typing import Any, Callable


def _run(args: list[str], *, env: dict[str, str] | None = None) -> tuple[int, str, str]:
    cmd = [sys.executable, "-m", "browser_fetch_router", *args]
    # Merge caller env into a fresh `os.environ` snapshot rather than
    # using `env or {**os.environ}`. The truthiness pattern silently
    # replaced the entire env when the caller passed even one override
    # — so passing `env={"BFR_AGENT": "test"}` would lose PATH /
    # PYTHONPATH / HOME and break the python -m launch on most boxes
    # (Gemini medium on commit f2a99d0). Merge semantics also remove
    # the Python-truthiness foot-gun: `env={}` is unambiguously
    # "inherit only" instead of also-falsy-and-falls-through.
    merged_env = {**os.environ, **(env or {})}
    proc = subprocess.run(
        cmd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env=merged_env,
        timeout=30,
    )
    return proc.returncode, proc.stdout, proc.stderr


def assert_status(args: list[str], expected_status: str) -> dict[str, Any]:
    code, out, err = _run(args)
    try:
        payload = json.loads(out)
    except (ValueError, json.JSONDecodeError):
        return {"ok": False, "reason": "non_json_output", "stdout": out[:500], "stderr": err[:500]}
    actual = payload.get("status")
    return {
        "ok": actual == expected_status,
        "expected_status": expected_status,
        "actual_status": actual,
        "exit_code": code,
    }


def assert_exit(args: list[str], expected_exit: int) -> dict[str, Any]:
    code, out, _err = _run(args)
    return {
        "ok": code == expected_exit,
        "expected_exit": expected_exit,
        "actual_exit": code,
        "stdout_preview": out[:200],
    }


CASES_LOCAL = [
    ("schema-emits-version", lambda: assert_exit(["schema", "--json"], 0)),
    ("doctor-ok", lambda: assert_exit(["doctor", "--json"], 0)),
    ("cleanup-empty", lambda: assert_exit(["cleanup", "--all", "--json"], 0)),
    # SSRF: literal IPs, obfuscated IPs, scheme blocking.
    ("blocks-loopback", lambda: assert_status(["read-web", "http://127.0.0.1/", "--json"], "unsafe_url_blocked")),
    ("blocks-zero", lambda: assert_status(["read-web", "http://0/", "--json"], "unsafe_url_blocked")),
    ("blocks-zero-net", lambda: assert_status(["read-web", "http://0.0.0.0/", "--json"], "unsafe_url_blocked")),
    ("blocks-ipv6-loopback", lambda: assert_status(["read-web", "http://[::1]/", "--json"], "unsafe_url_blocked")),
    ("blocks-ipv4-mapped-ipv6", lambda: assert_status(["read-web", "http://[::ffff:127.0.0.1]/", "--json"], "unsafe_url_blocked")),
    ("blocks-ula-ipv6", lambda: assert_status(["read-web", "http://[fc00::1]/", "--json"], "unsafe_url_blocked")),
    ("blocks-aws-metadata", lambda: assert_status(["read-web", "http://169.254.169.254/", "--json"], "unsafe_url_blocked")),
    ("blocks-file-scheme", lambda: assert_status(["read-web", "file:///etc/passwd", "--json"], "unsafe_url_blocked")),
    ("blocks-ftp-scheme", lambda: assert_status(["read-web", "ftp://example.com/", "--json"], "unsafe_url_blocked")),
    ("blocks-javascript-scheme", lambda: assert_status(["read-web", "javascript:alert(1)", "--json"], "unsafe_url_blocked")),
    ("blocks-int-encoded-loopback", lambda: assert_status(["read-web", "http://2130706433/", "--json"], "unsafe_url_blocked")),
    ("blocks-octal-loopback", lambda: assert_status(["read-web", "http://017700000001/", "--json"], "unsafe_url_blocked")),
    ("blocks-non-default-port", lambda: assert_status(["read-web", "http://example.com:22/", "--json"], "unsafe_url_blocked")),
    ("blocks-credentials", lambda: assert_status(["read-web", "http://user:pass@example.com/", "--json"], "unsafe_url_blocked")),
]


def run_acceptance(*, include_network: bool = False, include_paid: bool = False) -> dict[str, Any]:
    """Run the acceptance suite. Returns a structured summary including
    individual case verdicts. Network and paid cases are SKIPPED unless
    explicitly requested via flags."""
    cases: list[tuple[str, Callable[[], dict[str, Any]]]] = list(CASES_LOCAL)

    skipped: list[dict[str, Any]] = []
    if not include_network:
        skipped.append({
            "name": "example-read-network",
            "reason": "include_network not set",
        })
    else:
        cases.append((
            "example-read-network",
            lambda: assert_status(["read-web", "https://example.com/", "--json"], "ok"),
        ))
    if not include_paid:
        skipped.append({
            "name": "parallel-paid-extract",
            "reason": "include_paid not set",
        })
    else:
        cases.append((
            "parallel-paid-extract",
            lambda: assert_status(
                ["read-web", "https://example.com/", "--allow-paid", "--json"], "ok"
            ),
        ))

    started = time.time()
    results: list[dict[str, Any]] = []
    passed = 0
    failed = 0
    for name, fn in cases:
        case_started = time.time()
        try:
            outcome = fn()
            elapsed_ms = int((time.time() - case_started) * 1000)
            outcome["elapsed_ms"] = elapsed_ms
            outcome["name"] = name
            results.append(outcome)
            if outcome.get("ok"):
                passed += 1
            else:
                failed += 1
        except Exception as exc:
            results.append({
                "name": name,
                "ok": False,
                "reason": "case_raised",
                "exception": str(exc)[:300],
                "elapsed_ms": int((time.time() - case_started) * 1000),
            })
            failed += 1
    return {
        "passed": passed,
        "failed": failed,
        "skipped": len(skipped),
        "skipped_cases": skipped,
        "cases": results,
        "elapsed_seconds": round(time.time() - started, 3),
    }
