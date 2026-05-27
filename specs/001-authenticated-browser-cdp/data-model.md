# Data Model: Authenticated Browser CDP Access

## Browser Tab Target

Represents a browser page target returned by the DevTools `/json` endpoint.

Fields:
- `id`: Browser target identifier.
- `type`: Must be `page` for read/screenshot operations.
- `url`: Target URL; must be HTTP(S), approved, and not default-denied.
- `title`: Page title surfaced only after existing redaction rules allow it.
- `webSocketDebuggerUrl`: CDP WebSocket endpoint; must validate against the
  already-approved CDP base before connection.

Validation rules:
- Missing or non-page targets cannot be read or screenshotted.
- Non-HTTP(S) and default-denied URLs fail before CDP WebSocket connection.
- WebSocket URL credentials, host mismatch, or scheme mismatch fail before
  protocol command send.

## Approval Scope

Represents the existing authorization grant for tab reads and screenshots.

Fields:
- `scope`: Normalized exact, hostname, wildcard, or sentinel scope.
- `session_id`: Current BFR session identifier when scoped to a session.
- `persisted`: Whether the grant is stored beyond the current command.

Validation rules:
- `read_tab` and `screenshot_tab` must both route through the same approval
  resolver before any CDP command.
- Default-denied URLs require exact approval behavior already enforced by the
  approval module.

## CDP Command

Represents a JSON-RPC command sent over the validated DevTools WebSocket.

Fields:
- `id`: Monotonic integer request id.
- `method`: CDP method name such as `Page.getFrameTree`, `Page.createIsolatedWorld`,
  `Runtime.evaluate`, or `Page.captureScreenshot`.
- `params`: JSON object containing method parameters.
- `timeout`: Bounded wait for a response.

Validation rules:
- Responses must match the request id before being accepted.
- Responses with `error` become protocol failures with bounded public messages.
- Incoming messages must be bounded by named module constants.

## CDP Extraction Result

Represents successful browser inspection output.

Fields:
- `text`: Visible text returned by the page read path.
- `isolated_world`: Boolean evidence that an isolated world was used.
- `tab_id`: Browser target id.
- `screenshot_bytes`: PNG bytes for screenshot path, when screenshot succeeds.

Validation rules:
- Text output is capped by the existing `max_chars` behavior before envelope
  emission.
- Screenshot bytes are written only through existing safe-output validation and
  atomic file handling.
- Evidence must not include cookies, headers, raw CDP messages, or browser
  profile paths.

## Browser Capability Status

Represents truthful unavailable-provider or setup states.

Fields:
- `code`: Stable machine-readable error code.
- `message`: Human-readable cause and next action.
- `suggested_provider`: Concrete provider only when credentials and launch path
  are actually usable.
- `tier`: Existing task-risk classification evidence.

Validation rules:
- Missing declared dependencies and intentionally unavailable capabilities must
  produce different codes.
- `interactive-browser` must not report a provider as a fallback unless that
  provider can actually launch.
