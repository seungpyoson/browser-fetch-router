# browser-fetch-router HTTP transport contract

This document is the **single source of truth** for what "safe outbound
HTTP" means in the `browser_fetch_router` package. Every outbound HTTP
request in the package runs through `SafeHttpClient.request()` (CLI
command → providers / cdp / read_web → SafeHttpClient → wire). This
file enumerates the 17 invariants that chain MUST satisfy.

The companion file
`tests/browser_fetch_router/test_http_transport_contract.py` implements
every invariant as a parametrized test. Adding a new HTTP code path means
**ensuring it routes through `SafeHttpClient`** — the static guard
`test_no_adhoc_http_transport_in_production_code` fails the build for
any production import of `urllib.request`, `http.client.HTTPConnection`,
`socket.create_connection`, or the third-party `requests` package
outside `http_client.py`.

## Why this exists

PR #737 went through 15+ rounds of review. The persistence subsystem
closed via a similar contract (see
`browser-fetch-router-persistence-contract.md`); this is the same
move for HTTP transport. Rounds 3-7 each found one invariant of the
same subsystem (DNS pinning, redirect rejection, response size cap,
header injection, side-effect policy, IP-blocked patterns, CDP
redirect SSRF). The systematic alternative: enumerate ALL invariants
upfront and verify every code path satisfies all of them in one
closing pass — which is what this contract is.

## Threat model

The transport defends against:

- **SSRF** (Server-Side Request Forgery): an agent supplying a URL like
  `http://169.254.169.254/latest/meta-data/` (AWS IMDS) or
  `http://localhost:6379/` (local Redis) reaches a sensitive
  service. Defense: URL validation (H2) + per-DNS-answer block (H3) +
  redirect re-validation (H4, H17b).
- **DNS rebinding**: a hostname resolves to public IP at validation
  time, then to private IP at connection time. Defense: pin the
  resolved IP (resolved by `_default_resolver`, passed to connector;
  H3) and reject the hostname if ANY answer is private.
- **Header injection**: a caller smuggles `\r\nX-Injected: ...` into
  an `extra_headers` value. Defense: H10 byte-level validation +
  H9 transport-owned header rejection.
- **Response-size DoS / gzip bombs**: a malicious server returns a
  multi-GB body. Defense: H11 raw-byte cap enforced in the streaming
  read loop, before any decompression.
- **Phishing via embedded credentials**: a URL with `user:pass@host`
  silently sends auth to a phishing host. Defense: H15 reject any
  embedded credential.
- **TLS downgrade / verification bypass**: an attacker offers an
  invalid cert. Defense: H14 default to `ssl.create_default_context()`
  which enables hostname checking and CA validation.
- **Side-effect amplification**: an agent navigating to a server-
  redirected `/confirm?token=...` URL takes an unintended action.
  Defense: H17 — strict mode blocks side-effect-shaped initial URLs;
  default mode blocks server-redirected combinations even in non-
  strict.

Out of scope (intentionally NOT in this contract):

- **Provider-specific business logic** (jina vs reddit vs fxtwitter
  shaping) — covered by per-provider tests.
- **Cache semantics** — see read_web tests.
- **CDP isolated-world extraction** — pending Task 14; the websockets
  transport will need its own contract when implemented.

## The 17 invariants

| ID | Invariant | Why | Verified at |
|---|---|---|---|
| **H1** | Method allowlist `{GET, POST, PUT, DELETE, HEAD}`; anything else raises `unsupported_method` BEFORE DNS resolution. | TRACE/CONNECT/etc. expose latent vulnerabilities; explicit allowlist closes the surface. | `http_client.py:414` |
| **H2** | Every URL passes through `normalize_and_validate_url(allow_loopback=self._loopback_ok)` BEFORE any network. | One canonical URL gate — no per-caller parsing. | `:417` |
| **H3** | DNS resolved targets ALL pass `is_blocked_ip(allow_loopback=...)`; ANY private/IMDS/link-local answer rejects the hostname. | DNS rebinding defense — public-then-private split must not bypass. | `:503-505` |
| **H4** | `follow_redirects=False` raises `unexpected_redirect:<code>` on any 3xx. | CDP transport: a real DevTools server never redirects /json. | `:449-452` |
| **H5** | Cross-host redirect drops `Authorization` AND `Cookie`. | Credential leakage to redirect target. | `:462-469` |
| **H6** | Redirect loop capped at 10 hops; raises `redirect_hop_limit`. | Infinite-loop DoS. | `:433, :484` |
| **H7** | 301/302/303 coerce method → GET, drop body, drop `Content-*` headers. | Matches curl/browsers; body is gone. | `:473-481` |
| **H8** | 307/308 preserve method AND body. | RFC 7231 — PUT-as-PUT semantics. | (implicit, no override) |
| **H9** | `TRANSPORT_OWNED_HEADERS` (Host, Content-Length, Transfer-Encoding, TE, Trailer, Connection, Upgrade, Expect, User-Agent) rejected from caller (case-insensitive). | Request smuggling, framing desync. | `:203-215, :281-296` |
| **H10** | Header names: RFC 7230 token. Values: visible ASCII + SP + HTAB. CR/LF/NUL anywhere = `HostHeaderSmuggling`. | Header injection class. | `:259-280` |
| **H11** | Response size cap `max_bytes` enforced PRE-decompression in the streaming read loop; `ResponseTooLarge` raised on overflow. | Gzip bombs, accidental DoS via giant responses. | `:605-614` |
| **H12** | Socket and `HTTPResponse` closed in `finally` even when streaming raises. | Resource leak under exception. | `:534-538, :627-637` |
| **H13** | Constructor `timeout` propagated to the connector via `timeout=self.timeout` kwarg; `client.timeout` is the public attribute. | Per-request timeout — no infinite hangs on slow hosts. | `:520, :318` |
| **H14** | Default SSL context = `ssl.create_default_context()` (full verification). No `_create_unverified_context` / `CERT_NONE` in production source. | TLS verification — never disable. | `:336` |
| **H15** | Embedded credentials in URL (`user:pass@host`, `user@host`) rejected at URL validation. | Phishing; credential leakage. | `url_safety.py:230-231` |
| **H16** | C0 (0x00-0x1F) or C1 (0x7F-0x9F) control bytes anywhere in URL string raise BEFORE urlsplit. | Header/request-line injection via crafted URL. | `url_safety.py:225-226` |
| **H17a** | Initial URL with side-effect shape (action path + action query + one-time token): WARN in default mode, RAISE in strict. | User-typed URL: gentle gate. | `http_client.py:418-422` |
| **H17b** | Server-controlled REDIRECT target with side-effect shape: BLOCKED even in default mode. | Server-redirected dangerous targets are higher-bar. | `:459` |

## Hidden invariants discovered during the closing pass

- **`validate_redirect` carries `allow_loopback`**. Pre-PR-#737-r15-final
  the redirect target was always validated with
  `allow_loopback=False`, which created a hidden coupling: any
  `SafeHttpClient(loopback_ok=True)` caller had to also use
  `follow_redirects=False`. Closing the gap structurally lets a
  future caller pair the two flags as needed.

## Static guard

`test_no_adhoc_http_transport_in_production_code` walks the AST of
every `.py` file in `browser_fetch_router/` (excluding `http_client.py`)
and rejects bypasses at three levels — round-17 Class B closure folded
the original whole-module ban into a per-name structure so legitimate
non-HTTP uses of multi-purpose modules (`socket.getaddrinfo` for DNS in
`cdp.py`) stay allowed while every bypass primitive is caught.

**1. `HTTP_BANNED_TOPLEVEL_IMPORTS` — whole-module bans.** Modules with
no legitimate non-HTTP use:

- `import urllib.request`
- `import requests`

**2. `HTTP_BANNED_IMPORT_FROM_NAMES` — per-name bans inside
multi-purpose modules.** A `*` key means "any name from this module."

- `from urllib.request import …` (any name) — wholesale ban via `*`
- `from requests import …` (any name) — wholesale ban via `*`
- `from http.client import HTTPConnection | HTTPSConnection`
- `from socket import create_connection | socket | socketpair`

`from socket import getaddrinfo` is intentionally allowed — DNS-only
resolution in `cdp.py` does not bypass any SafeHttpClient invariant.

**3. `HTTP_BANNED_ATTRIBUTE_CHAINS` — dotted-form call bans.**
For callers who use the dotted form rather than from-import:

- `urllib.request.urlopen(...)`, `urllib.request.Request(...)`
- `http.client.HTTPConnection(...)`, `http.client.HTTPSConnection(...)`
- `socket.create_connection(...)`
- `socket.socket(...)`, `socket.socketpair(...)` (raw constructors —
  round-17 Class B closure)

AST walk (not regex) so docstring text mentioning these names doesn't
false-positive.

**Static-guard limitations** (F-17e, round-17 followup): the AST walk
catches the literal call-site forms above, but cannot catch dynamic
bypasses:

- Aliased imports: `from socket import socket as _S; _S()` —
  the chain check looks for `socket.socket`, not the alias `_S`.
- Runtime attribute access: `getattr(socket, "create_connection")`.
- `importlib.import_module("urllib.request")` — string-based imports.

These are inherent limits of static analysis. The guard is a
lint-level enforcement against accidental copy-paste, not a
cryptographic containment. A determined developer can bypass any
static guard. The defense relies on code review to catch deliberate
or creative bypasses; the static guard catches the cases code review
might miss because they look unremarkable.

The scan logic lives in module-level helpers
(`find_http_transport_offenders`,
`HTTP_BANNED_TOPLEVEL_IMPORTS`,
`HTTP_BANNED_IMPORT_FROM_NAMES`,
`HTTP_BANNED_ATTRIBUTE_CHAINS`) so the round-17 reproduction tests can
exercise the production guard against synthetic offender directories
using the same source of truth — extending any banned set
automatically tightens both the production test and every reproduction.

## Maintenance

**Adding a new outbound HTTP entry point**:

1. Use `SafeHttpClient` exclusively. The static guard prevents
   raw-primitive bypasses at build time.
2. Choose `loopback_ok=` carefully — only the CDP path (user's local
   browser) sets `True`. `follow_redirects=False` is the right
   pairing in nearly every loopback case.
3. Run the contract suite — every applicable invariant runs against
   your new code path automatically (the suite tests the SafeHttpClient
   contract, not per-caller behavior).

**Adding a new invariant**:

1. Document it here with an ID + verification site.
2. Add a parametrized test in the contract suite.
3. Run the suite — every existing entry inherits the new check.

**Removing or relaxing an invariant** is a security boundary change
— it MUST go through code review with explicit threat-model
discussion, not a silent commit. Update this doc in the same commit.
