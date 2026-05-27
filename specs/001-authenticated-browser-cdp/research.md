# Research: Authenticated Browser CDP Access

## Decision: Implement one synchronous CDP WebSocket client in `cdp.py`

**Rationale**: Browser Fetch Router is a synchronous CLI. The official
`websockets` documentation provides a threading client API via
`websockets.sync.client.connect`, with context-manager lifecycle and blocking
`send`/`recv` calls. This fits the existing CLI without introducing an asyncio
runner or a second execution model.

**Alternatives considered**:
- `asyncio` CDP client: rejected because the current CLI and tests are
  synchronous and adding an event-loop boundary would increase complexity.
- Hand-rolled WebSocket framing: rejected because the project should use a
  proven protocol library for WebSocket correctness.

## Decision: Add `websockets>=16,<17` as a runtime dependency

**Rationale**: `pyproject.toml` requires dependencies to be declared only when
runtime code imports them. This feature imports the `websockets.sync.client`
module, so the dependency belongs in the same implementation PR. Pinning to the
current major line avoids unreviewed future major API changes while allowing
patch/minor fixes.

**Alternatives considered**:
- Leave dependency optional and return setup errors: rejected because the primary
  success path must work after `pip install .`.
- Reuse a transitive dependency: rejected because there is no declared
  WebSocket-capable dependency today.

## Decision: Validate `webSocketDebuggerUrl` against the validated CDP base

**Rationale**: `fetch_tab_list` already validates the HTTP CDP base URL and
retrieves tab metadata through `SafeHttpClient`. A malicious or compromised CDP
endpoint could still return a `webSocketDebuggerUrl` pointing elsewhere. The
WebSocket connection must therefore require `ws` or `wss`, no embedded
credentials, and host/port/scheme consistency with the validated CDP base before
sending any protocol command.

**Alternatives considered**:
- Trust Chrome tab metadata: rejected because the project already treats CDP as
  a security boundary and rejects redirects/rebinding in the HTTP layer.
- Re-run generic URL safety only on the WebSocket URL: rejected because the
  expected CDP peer is already known; matching the base is tighter and simpler.

## Decision: Extract text through an isolated world and `Runtime.evaluate`

**Rationale**: Chrome DevTools Protocol documents `Page.createIsolatedWorld` for
creating an execution context for a frame and `Runtime.evaluate` for returning
by-value evaluation results. The read should use the main frame, create a named
isolated world with universal access disabled, evaluate bounded visible-text
logic, and return plain text plus extraction evidence.

**Alternatives considered**:
- Evaluate directly in the page context: rejected because page scripts should
  not be able to intercept or shape the extraction mechanism.
- Use screenshot/OCR for text: rejected because issue #6/#7 require text
  extraction and screenshots are a more sensitive fallback.

## Decision: Treat screenshot as the same CDP capability class

**Rationale**: Claude root-cause review confirmed that `fetch_tab_screenshot`
has the same unconditional stub and misleading dependency message as
`fetch_tab_text`. CDP documents `Page.captureScreenshot` returning base64 image
data. The implementation should either make this command work through the same
validated CDP client or return a precise unsupported-capability error; it must
not retain the old dependency-only message.

**Alternatives considered**:
- Leave screenshot untouched: rejected because it preserves the same public
  reliability defect in an adjacent command.
- Add a separate screenshot transport: rejected because that would create a dual
  path for the same browser target.

## Decision: Do not implement `interactive-browser` provider launch in this PR

**Rationale**: The spec only needs one authenticated browser path to work.
`interactive-browser` currently gates local, Browserbase, and cloud paths behind
pending launch states. This PR should make those failures accurate and explicit,
not introduce a second large browser automation integration.

**Alternatives considered**:
- Implement local `browser-use` launch now: rejected as a separate provider
  lifecycle/sandbox feature.
- Hide `interactive-browser`: rejected because the CLI command is public; it
  should remain but report truthfully.

## Primary References

- websockets 16.0 docs: synchronous client examples and threading model.
- Chrome DevTools Protocol Page domain: `Page.createIsolatedWorld` and
  `Page.captureScreenshot`.
- Chrome DevTools Protocol Runtime domain: `Runtime.evaluate`.
