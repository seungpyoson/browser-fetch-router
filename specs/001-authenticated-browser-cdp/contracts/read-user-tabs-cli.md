# CLI Contract: Authenticated Browser CDP Access

## `read-user-tabs read`

Command:

```bash
browser-fetch-router read-user-tabs read <target> --json \
  --approval-scope hostname:<host> \
  --max-chars <n>
```

Success envelope:

```json
{
  "command": "read-user-tabs",
  "status": "ok",
  "url": "https://example.test/page",
  "title": "Example",
  "content_markdown": "Visible page text",
  "evidence": {
    "cdp_isolated_world": true,
    "tab_id": "target-id"
  }
}
```

Required failure envelopes:

- Missing approval: `status: "approval_required"`, `error.code: "approval_required_for_tab"`.
- CDP unreachable: `status: "tool_setup_failed"`, `error.code: "cdp_unreachable"`.
- Missing WebSocket runtime dependency: `status: "tool_setup_failed"`, `error.code: "cdp_websocket_dependency_missing"`.
- Invalid or mismatched debugger WebSocket URL: `status: "tool_setup_failed"`, `error.code: "cdp_websocket_url_invalid"` or `"cdp_websocket_url_mismatch"`.
- CDP protocol failure: `status: "tool_setup_failed"`, `error.code: "cdp_protocol_error"` with a bounded public message.
- Text extraction failure: `status: "tool_setup_failed"`, `error.code: "cdp_text_extraction_failed"`.

Contract rules:

- No success path may bypass `_resolve_and_authorize_tab`.
- Dependency guidance may name `websockets` only when importing the declared
  package fails.
- Evidence must not include raw CDP responses, cookies, headers, or profile paths.

## `read-user-tabs screenshot`

Command:

```bash
browser-fetch-router read-user-tabs screenshot <target> --json \
  --approval-scope hostname:<host> \
  --output /tmp/screenshot.png
```

Success envelope:

```json
{
  "command": "read-user-tabs",
  "status": "ok",
  "url": "https://example.test/page",
  "artifacts": [
    {
      "path": "/tmp/screenshot.png",
      "kind": "image/png"
    }
  ]
}
```

Required failure envelopes:

- Missing approval: same approval contract as text reads.
- Unsafe output: existing `unsafe_output_destination` contract.
- Missing WebSocket runtime dependency: `cdp_websocket_dependency_missing`.
- Screenshot unsupported or protocol failure: `cdp_screenshot_failed` or
  `cdp_protocol_error`; must not use the old dependency-only message unless the
  dependency is actually missing.

Contract rules:

- Output path validation happens before CDP capture.
- Approval happens before CDP capture.
- The output file is written through existing atomic 0600 behavior.

## `interactive-browser`

Command:

```bash
browser-fetch-router interactive-browser "<task>" --json --confirm-irreversible <id>
```

Unavailable provider envelope:

```json
{
  "command": "interactive-browser",
  "status": "tool_setup_failed",
  "error": {
    "code": "browser_provider_unavailable",
    "message": "No configured interactive browser provider can launch in this install."
  },
  "evidence": {
    "tier": "C"
  }
}
```

Contract rules:

- Provider-specific credential errors may remain provider-specific.
- `suggested_provider` is present only when the provider can actually launch
  after the documented opt-in and credentials are supplied.
- The command must not claim to be a fallback for authenticated inspection while
  all launch paths are pending.
