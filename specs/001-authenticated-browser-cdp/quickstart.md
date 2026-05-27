# Quickstart: Authenticated Browser CDP Access

## Local Test Page Scenario

1. Install the package from a clean environment:

   ```bash
   python3 -m pip install .
   browser-fetch-router --help
   ```

2. Start a local HTTP page that contains known visible text:

   ```bash
   python3 -m http.server 8765 --directory /tmp/bfr-cdp-fixture
   ```

3. Launch Chrome or Chromium with DevTools and a temporary profile:

   ```bash
   "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
     --remote-debugging-port=9222 \
     --user-data-dir=/private/tmp/bfr-cdp-profile \
     "http://127.0.0.1:8765/index.html"
   ```

   On Linux, use the installed Chrome or Chromium binary instead:

   ```bash
   google-chrome \
     --remote-debugging-port=9222 \
     --user-data-dir=/tmp/bfr-cdp-profile \
     "http://127.0.0.1:8765/index.html"
   ```

   If the host uses Chromium, replace `google-chrome` with
   `chromium-browser` or `chromium`.

4. Verify tab listing works:

   ```bash
   env BFR_AGENT=Codex BFR_SESSION_ID=bfr-cdp-quickstart \
     browser-fetch-router read-user-tabs list --json --show-all
   ```

5. Verify approved text extraction:

   ```bash
   env BFR_AGENT=Codex BFR_SESSION_ID=bfr-cdp-quickstart \
     browser-fetch-router read-user-tabs read active \
     --json \
     --approval-scope hostname:127.0.0.1 \
     --max-chars 12000
   ```

   Expected result: `status: ok`, `content_markdown` contains the known visible
   fixture text, and evidence reports isolated-world CDP extraction.

6. Verify screenshot behavior:

   ```bash
   env BFR_AGENT=Codex BFR_SESSION_ID=bfr-cdp-quickstart \
     browser-fetch-router read-user-tabs screenshot active \
     --json \
     --approval-scope hostname:127.0.0.1 \
     --output /tmp/bfr-cdp-screenshot.png
   ```

   Expected result: either `status: ok` with an image artifact or a precise
   unsupported-capability error. It must not mention `websockets` unless the
   declared dependency is actually missing.

## Negative Checks

- Stop Chrome and verify `read-user-tabs read active --json` returns
  `cdp_unreachable`.
- Omit `--approval-scope` and verify `approval_required_for_tab`.
- Simulate missing WebSocket dependency in a controlled test environment and
  verify `cdp_websocket_dependency_missing`.
- Run `interactive-browser` with confirmation and no usable provider; verify it
  reports unavailable provider status without claiming a fallback can launch.

## Required Verification

```bash
python3 -m pytest tests/browser_fetch_router/test_cdp.py tests/browser_fetch_router/test_read_user_tabs.py tests/browser_fetch_router/test_interactive.py
python3 -m pytest tests/browser_fetch_router
```

Package installability must also be checked from outside the repository:

```bash
python3 -m pip install .
browser-fetch-router --help
```
