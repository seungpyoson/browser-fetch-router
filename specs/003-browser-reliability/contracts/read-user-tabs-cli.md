# Contract: read-user-tabs Setup And Use

## Commands

```bash
browser-fetch-router read-user-tabs list --json
browser-fetch-router read-user-tabs read <url-or-tab-id|active> --json --approval-scope <scope>
browser-fetch-router read-user-tabs screenshot <url-or-tab-id|active> --json --approval-scope <scope> --output <path>
```

## CDP Setup Contract

When no CDP endpoint is reachable at `http://127.0.0.1:9222`, the CLI must return:

- `command`: `read-user-tabs`
- `status`: `tool_setup_failed`
- `error.code`: `cdp_unreachable`
- `evidence.cdp_base`: attempted CDP base URL
- A user-facing setup hint or docs pointer for starting a loopback temporary Chrome profile

The setup path must use:

- `--remote-debugging-address=127.0.0.1`
- `--remote-debugging-port=9222`
- `--user-data-dir=<temporary-profile>`
- No default instruction to expose a normal browser profile
- Do not use the normal browser profile for CDP setup

## Approval Contract

- Listing default-deny tabs stays redacted unless the broad list approval is present.
- Reading page content requires a matching `exact:` or `hostname:` approval scope.
- Screenshots require approval and write only to the requested output path after containment validation.

## Acceptance Commands

```bash
curl -sS http://127.0.0.1:9222/json/version
browser-fetch-router read-user-tabs list --json
browser-fetch-router read-user-tabs list --all --approval-scope exact:list-all-tabs --persist-approval --show-all --json
browser-fetch-router read-user-tabs read active --approval-scope hostname:www.wikipedia.org --max-chars 1000 --json
browser-fetch-router read-user-tabs screenshot active --approval-scope hostname:www.wikipedia.org --output "${TMPDIR:-/tmp}/bfr-active.png" --json
```
