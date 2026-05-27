# Feature Specification: Authenticated Browser CDP Access

**Feature Branch**: `001-authenticated-browser-cdp`  
**Created**: 2026-05-27  
**Status**: Draft  
**Input**: User description: "Make browser-fetch-router robust for authenticated browser inspection; consolidate GitHub issues #6 and #7 after root-cause review."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Read Approved Authenticated Tab Text (Priority: P1)

An agent can read visible text from a user-approved HTTP(S) browser tab that is
already reachable through Chrome DevTools, including pages that require the
user's authenticated browser session.

**Why this priority**: This is the blocked primary workflow from issues #6 and
#7. Public `read-web` cannot inspect account-only logs, settings, or status
pages; `read-user-tabs read` must be the working authenticated path.

**Independent Test**: Start a temporary browser profile with DevTools enabled,
open a local HTTP(S) page with known visible text, grant a hostname approval
scope, run `read-user-tabs read active --json`, and verify `status: ok` with
the visible text returned.

**Acceptance Scenarios**:

1. **Given** a reachable DevTools endpoint with an active approved HTTP(S) tab,
   **When** the user runs `browser-fetch-router read-user-tabs read active --json --approval-scope hostname:<host>`,
   **Then** the command returns `status: ok`, includes capped visible page text,
   and records CDP extraction evidence without exposing credentials.
2. **Given** a reachable DevTools endpoint with an active HTTP(S) tab but no
   matching approval scope, **When** the user runs the same read command,
   **Then** the command returns `approval_required` and does not read page text.

---

### User Story 2 - Receive Accurate Browser Capability Failures (Priority: P2)

An agent receives precise setup or unsupported-capability errors when browser
inspection cannot run, instead of being told to install a dependency that would
not change the result.

**Why this priority**: Issues #6 and #7 show a misleading `websockets`
dependency message even though the selected source path is an unconditional
pending implementation stub. Users need errors that lead to the real next
action.

**Independent Test**: Exercise missing transport, unreachable CDP, unsupported
provider, and unavailable interactive-browser states, then assert each JSON
error names the actual cause and a concrete next action.

**Acceptance Scenarios**:

1. **Given** the CDP transport required for a live read is unavailable,
   **When** the user runs `read-user-tabs read`,
   **Then** the response identifies the missing transport dependency and tells
   the user how to install the package version that declares it.
2. **Given** a browser provider path is intentionally unavailable,
   **When** the user runs `interactive-browser` after explicit confirmation,
   **Then** the response states that no local or hosted provider was launched
   and does not present that path as a working fallback.

---

### User Story 3 - Use The Same CDP Capability For Approved Screenshots (Priority: P3)

An agent can use the same approved CDP capability surface for screenshots, or
gets an accurate unsupported-capability error if screenshot capture cannot run.

**Why this priority**: Root-cause review found `read_tab` and `screenshot_tab`
share the same CDP stub class and the same misleading dependency message. Fixing
only text reads would leave the same reliability defect in the adjacent public
command.

**Independent Test**: With an approved reachable tab, run the screenshot command
to a safe output path and verify either an `ok` image artifact from CDP or a
precise unsupported-capability error that does not mention unrelated
dependencies.

**Acceptance Scenarios**:

1. **Given** a reachable approved tab and safe output path,
   **When** the user runs the screenshot command,
   **Then** the command either writes a protected image artifact from the tab or
   returns a precise unsupported-capability error without reading before approval.
2. **Given** a default-denied page or missing approval scope,
   **When** the user runs the screenshot command,
   **Then** approval fails before any screenshot capture is attempted.

### Edge Cases

- CDP endpoint is unreachable, redirects unexpectedly, or returns a malformed
  tab list.
- Active tab is non-HTTP(S), default-denied, missing a websocket debugger URL,
  or no longer exists between list and read.
- Page has no `document.body`, very large visible text, late-loading text, or
  script errors during extraction.
- Runtime dependency is absent from the installed package.
- Interactive browser provider credentials are absent or hosted-browser opt-in
  is missing.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `read-user-tabs list` MUST continue to list reachable tabs without
  granting read permission for tab content.
- **FR-002**: `read-user-tabs read` MUST extract visible text from an approved
  HTTP(S) page-type tab through the browser's DevTools connection.
- **FR-003**: `read-user-tabs read` MUST require the existing approval model
  before extracting text and MUST preserve default-deny behavior.
- **FR-004**: Extracted text MUST respect the caller's maximum character limit
  and include an explicit truncation marker when capped.
- **FR-005**: CDP text extraction MUST isolate the read from page application
  code so page scripts cannot intercept or alter the extraction mechanism.
- **FR-006**: `read-user-tabs screenshot` MUST use the same authorization
  boundary as text reads and MUST NOT capture before approval succeeds.
- **FR-007**: CDP text and screenshot failures MUST distinguish unreachable CDP,
  missing declared runtime dependency, unsupported browser target, and
  intentionally unavailable capability.
- **FR-008**: `interactive-browser` MUST report provider availability truthfully
  after confirmation and MUST NOT imply a fallback can inspect authenticated
  pages when no provider path launches.
- **FR-009**: Browser inspection responses MUST NOT include raw cookies,
  authorization headers, session tokens, or browser profile paths beyond
  already-approved page URL/title fields.
- **FR-010**: Verification MUST include behavior tests for successful approved
  text extraction and negative-path error taxonomy.

### Key Entities

- **Browser Tab Target**: A page-type tab exposed by DevTools with an identifier,
  URL, title, and debugger endpoint.
- **Approval Scope**: A one-time or persisted grant that authorizes reading a
  specific page, hostname, or supported sentinel scope.
- **CDP Extraction Result**: The extracted visible content or image artifact plus
  bounded evidence about the target and extraction mode.
- **Browser Capability Status**: A structured status explaining whether a CDP or
  interactive-browser capability is ready, missing setup, unsupported, or
  intentionally unavailable.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In an end-to-end temporary browser profile test, an approved active
  tab containing known visible text returns `status: ok` and includes that text.
- **SC-002**: Installing the package with declared dependencies is sufficient for
  the successful CDP text-read test; no manual dependency injection is required.
- **SC-003**: Negative-path tests cover at least four distinct failure classes:
  CDP unreachable, approval missing, missing runtime dependency, and provider
  unavailable.
- **SC-004**: No error message for an implemented or intentionally unavailable
  path tells the user to install a dependency unless installing that dependency
  is sufficient to change the outcome.
- **SC-005**: The repository verification gate `python3 -m pytest tests/browser_fetch_router`
  passes, except for any documented unrelated platform-permission failure.

## Assumptions

- Users can launch a temporary Chrome or Chromium profile with DevTools enabled
  when they want authenticated browser access.
- The first working authenticated browser path for this feature is
  `read-user-tabs`; making `interactive-browser` launch a full browser provider
  is out of scope unless required to keep error reporting truthful.
- Screenshots are included because the same public module exposes a screenshot
  path with the same root-cause class; screenshot capture may be implemented or
  reported as unsupported, but it must not keep the misleading dependency error.
- Install-agent portability from issues #4 and #5 is a separate feature and is
  intentionally excluded from this specification.
