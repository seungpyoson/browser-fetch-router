import json

from browser_fetch_router import cli


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
