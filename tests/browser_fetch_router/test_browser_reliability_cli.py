import json
import os
import subprocess
import sys
from pathlib import Path

from browser_fetch_router import cli

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_cli_process(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env["PYTHONPATH"] = str(_REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "browser_fetch_router", *args],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


class _Response:
    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.text = text


class _TextClient:
    def get_text(self, url: str, *, max_bytes: int) -> _Response:
        return _Response(
            200,
            "\n".join(
                [
                    "Title: Example Domain",
                    "",
                    "# Example Domain",
                    "",
                    "This domain is for use in illustrative examples in documents.",
                    "You may use this domain in literature without prior coordination or asking for permission.",
                    "",
                    "[More information...](https://www.iana.org/domains/example)",
                ]
            ),
        )


class _RedditClient:
    def get_text(self, url: str, *, max_bytes: int) -> _Response:
        if "/comments/" in url:
            payload = [
                {
                    "data": {
                        "children": [
                            {
                                "data": {
                                    "title": "Post title",
                                    "selftext": "Post body",
                                }
                            }
                        ]
                    }
                },
                {"data": {"children": [{"data": {"author": "alice", "body": "Comment"}}]}},
            ]
        else:
            payload = {
                "data": {
                    "children": [
                        {
                            "data": {
                                "title": "Python listing title",
                                "author": "releasebot",
                                "subreddit": "python",
                                "score": 7,
                                "num_comments": 2,
                            }
                        }
                    ]
                }
            }
        return _Response(200, json.dumps(payload))


def test_cli_subprocess_helper_runs_schema_from_temp_home(tmp_path):
    result = _run_cli_process(tmp_path, "schema", "--json")

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "browser-fetch-router.v1"


def test_read_web_cli_accepts_short_valid_public_page(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("HOME", str(tmp_path))
    from browser_fetch_router import read_web

    monkeypatch.setattr(read_web, "SafeHttpClient", _TextClient)

    rc = cli.main(["read-web", "https://example.com/", "--json", "--no-cache"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    assert payload["provider"] == "jina-reader"
    assert payload["quality"]["is_short_valid_content"] is True


def test_read_web_cli_handles_reddit_listing_and_post_urls(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    from browser_fetch_router import read_web

    monkeypatch.setattr(read_web, "SafeHttpClient", _RedditClient)

    listing_rc = cli.main([
        "read-web",
        "https://www.reddit.com/r/python/",
        "--json",
        "--no-cache",
    ])
    listing = json.loads(capsys.readouterr().out)

    post_rc = cli.main([
        "read-web",
        "https://www.reddit.com/r/python/comments/abc/post_title/",
        "--json",
        "--no-cache",
    ])
    post = json.loads(capsys.readouterr().out)

    assert listing_rc == 0
    assert listing["status"] == "ok"
    assert listing["provider"] == "reddit-json"
    assert "Python listing title" in listing["content_markdown"]
    assert post_rc == 0
    assert post["status"] == "ok"
    assert post["provider"] == "reddit-json"
    assert "Post body" in post["content_markdown"]


def test_paid_acceptance_case_uses_fallback_eligible_url(monkeypatch):
    from browser_fetch_router import acceptance

    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_assert_status(
        args: list[str],
        expected_status: str,
        **kwargs: object,
    ) -> dict[str, object]:
        calls.append((args, kwargs))
        return {"ok": True, "expected_status": expected_status, "actual_status": expected_status}

    monkeypatch.setattr(acceptance, "assert_status", fake_assert_status)
    monkeypatch.setattr(acceptance, "assert_exit", lambda *_args: {"ok": True})

    result = acceptance.run_acceptance(include_network=True, include_paid=True)

    assert result["failed"] == 0
    paid_calls = [
        call for call in calls if "--allow-paid" in call[0]
    ]
    assert paid_calls == [
        (
            [
                "read-web",
                "https://raw.githubusercontent.com/octocat/Hello-World/master/README",
                "--allow-paid",
                "--json",
                "--no-cache",
            ],
            {"timeout": acceptance.PAID_ACCEPTANCE_TIMEOUT_SECONDS},
        )
    ]


def test_acceptance_status_check_allows_custom_timeout(monkeypatch):
    from browser_fetch_router import acceptance

    captured: dict[str, object] = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["timeout"] = kwargs["timeout"]

        class _Proc:
            returncode = 0
            stdout = '{"status": "ok"}'
            stderr = ""

        return _Proc()

    monkeypatch.setattr(acceptance.subprocess, "run", fake_run)

    result = acceptance.assert_status(
        ["read-web", acceptance.PAID_FALLBACK_SMOKE_URL, "--allow-paid", "--json"],
        "ok",
        timeout=90,
    )

    assert result["ok"] is True
    assert captured["timeout"] == 90
