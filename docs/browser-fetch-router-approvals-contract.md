# browser-fetch-router approvals contract

This document is the **single source of truth** for "safe authorization"
in the `browser_fetch_router` package. Every handler that reads from
the user's authenticated browser state via CDP gates the request behind
`can_read_url(url, persistent_scopes, exact_one_time=...)` (or, for
the meta-permission "list every tab," a sentinel-scope check via
`list_active_scopes`). This file enumerates the 20+ invariants that
chain MUST satisfy.

The companion file
`tests/browser_fetch_router/test_approvals_contract.py` implements
every invariant as a parametrized test. Adding a new authorization-
relevant code path means routing through one of the canonical entry
points — the static guards `test_f1_no_direct_approval_store_access_outside_approvals_module`
and `test_f2_user_tab_handlers_route_through_can_read_url` fail the
build for direct store access or for unguarded user-tab fetch handlers.

## Why this exists

PR #737 went through 16+ rounds of review. Three prior subsystems
closed via similar contracts (persistence on `201a050`, HTTP transport
+ lifecycle on `165c257`). Approvals had the second-most accumulated
review rounds:

  - r6-02 silent-dead-approval (malformed hostname/wildcard)
  - r7-02 exact: URL SSRF storage bypass
  - r9-01 unknown-kind defense-in-depth
  - r11-i02 can_read_url exact-one-time wrapping
  - r11-i03 empty exact value rejection
  - r12-i05 missing kind separator
  - r15-02 regional-subdomain default-deny bypass

Each round found one invariant in isolation. This contract enumerates
all of them in one closing pass.

## Threat model

Approvals defends against:

- **Silent-dead approvals**: an agent runs `add-approval foo`, no error,
  but no URL ever matches because `foo` was malformed. Defense:
  `normalize_scope` raises `InvalidScope` on every grammar violation
  upfront (A1-A5 + E5).
- **Exact-URL SSRF storage**: an agent stores
  `exact:http://169.254.169.254/...` as a "legitimate" exact one-time
  approval; `can_read_url` then returns True for the IMDS URL, which
  the transport blocks as defense-in-depth — but the broken invariant
  becomes directly exploitable once a code path bypasses transport
  validation. Defense: A4 — exact: URL scopes go through
  `normalize_and_validate_url(allow_loopback=True)`, so non-loopback
  SSRF targets are rejected at storage time. Loopback is intentionally
  permitted for local-browser CDP (A4b).
- **Default-deny bypass via subdomain**: a host-sensitive entry like
  `console.aws.amazon.com` listed without its `*.console.aws.amazon.com`
  wildcard counterpart lets `us-east-1.console.aws.amazon.com` (and
  every other regional console) bypass the matcher (which does exact
  equality for non-wildcards). Defense: D1 + r15-02 static guard —
  every bare host has its `*.<host>` sibling.
- **Cross-session privilege leak**: session-scoped (`persisted=False`)
  approvals visible to other sessions would let one CLI invocation
  silently inherit another's grants. Defense: E2 — session-scoped
  approvals filter on `session_id` match.
- **Stored-corruption crash**: a hand-planted or vintage record with
  unknown kind, malformed scope, or invalid `expires_at` could crash
  the auth check. Defense: C1 + C2 + E6 — `approval_matches` wraps
  normalization in try/except and returns False; `list_active_scopes`
  silently skips records with malformed `expires_at`.
- **Direct store bypass**: a handler that reads
  `~/.config/browser-fetch-router/approvals.json` directly bypasses
  the SentinelLock + grammar validation + expiration filter. Defense:
  F1 static guard.
- **Unguarded fetch handler**: a new user-tabs handler added without
  routing through `can_read_url` / `is_default_denied` /
  `list_active_scopes` would silently fetch authenticated content.
  Defense: F2 static guard.

Out of scope:

- **`read-web` URL-shaped fetches**: `read-web` uses SafeHttpClient
  (cookieless) + url_safety SSRF defense. Fetching `https://gmail.com/`
  without cookies just returns a login page — no authenticated content
  reachable. Default-deny intentionally does NOT apply (would block
  public web fetching for no security gain). Architectural decision.
- **`interactive-browser` task execution**: separate approval model
  (hosted-browser scope) for live agent task execution. Distinct from
  the URL-shaped read pipeline.

## The 20 invariants

| ID | Invariant | Why | Verified at |
|---|---|---|---|
| **A1** | `normalize_scope` requires the `kind:value` separator. Kindless input raises `InvalidScope("missing_kind_separator")`. | round-12 i05 silent-dead. | `approvals.py:97-99` |
| **A2** | `kind` ∈ VALID_SCOPE_KINDS (`exact`, `hostname`, `wildcard`). Anything else raises `InvalidScope("unknown_scope_kind")`. | round-9 r9-01 silent-dead. | `:148-150` |
| **A3** | hostname/wildcard scopes route through `_canonicalize_hostname_scope` which rejects ports, paths, queries, fragments, double `**.`, leading dots, embedded whitespace, and similar shape errors. | round-6 r6-02 silent-dead. | `:42-77` |
| **A4** | `exact:<URL>` scopes go through `normalize_and_validate_url(allow_loopback=True)`. Non-loopback SSRF targets rejected at storage. | round-7 r7-02 P1-security. | `:127-136` |
| **A4b** | Loopback URLs in `exact:` scopes are deliberately PERMITTED (CDP intended path). | Required for local-browser CDP scopes. | `:135` (via `allow_loopback=True`) |
| **A5** | `exact:` with empty value raises `InvalidScope("empty_exact_scope")`. | round-11 i03 silent-dead. | `:128-133` |
| **A6** | Valid scopes canonicalize idempotently — `normalize_scope(normalize_scope(x)) == normalize_scope(x)`. | Round-trip stability. | (behavior, full grammar) |
| **B1** | `can_read_url` precedence: default-deny wins over hostname/wildcard scopes. An `wildcard:gmail.com` approval still cannot read `gmail.com/inbox`. | Compromise blast-radius limit. | `:204-214` |
| **B2** | `exact_one_time` overrides default-deny. Only way to override default-deny — explicit user-consent ceremony. | Operator escape valve. | `:204-211` |
| **B3** | Default state is DENY: no matching scope + no exact-one-time = False. | Secure default. | `:213-214` |
| **B4** | A non-default-denied URL with a matching persistent scope returns True. | Standard happy path. | `:213-214` |
| **C1** | `approval_matches` is defense-in-depth — catches `InvalidScope` AND `UnsafeUrl` from `normalize_scope` and returns False. | Stored-corruption resilience. | `:163-167` |
| **C2** | `can_read_url` wraps `normalize_scope` in try/except for the exact-one-time loop — corrupt entries skipped, valid siblings still match. | Round-11 i02. | `:205-209` |
| **D1** | Every bare hostname in `HOST_SENSITIVE_PATTERNS` has its `*.<host>` wildcard counterpart. (Cross-references the round-15 r15-02 static guard.) | Regional-subdomain bypass class. | `default_deny.py:14-36` |
| **D2** | Default-deny catches subdomain variants (`www.gmail.com`, `m.gmail.com`, `us-east-1.console.aws.amazon.com`). | Class regression test. | `:104-112` |
| **E1** | `add_approval(persisted=True)` then `list_active_scopes` from any session returns the persisted scope. Persisted scopes are session-independent. | Persistent grants must outlive sessions. | `:266-290`, `:303-304` |
| **E2** | `add_approval(persisted=False)` is visible ONLY to the owning session_id. | Cross-session privilege leak defense. | `:307-314` |
| **E3** | Session-scoped approvals expire after `SESSION_TTL_SECONDS = 8h`. Past TTL, not returned even to owning session. | Bounded session lifetime. | `:296`, `:308-313` |
| **E4** | `revoke_scope` removes the scope from the store; subsequent `list_active_scopes` no longer returns it. | Standard revocation contract. | `:318-329` |
| **E5** | `add_approval` calls `normalize_scope` upfront — malformed scopes raise `InvalidScope` BEFORE writing to disk. | Write-side gate. | `:273` |
| **E6** | `list_active_scopes` silently skips records with malformed `expires_at`. | Stored-corruption resilience on read. | `:309-312` |

Cross-subsystem invariants (covered by other contracts):

| ID | Invariant | Contract |
|---|---|---|
| Lock | `add_approval` / `revoke_scope` use `SentinelLock` on a sibling lock file (`.approvals.lock`). | persistence (D — concurrency safety) |
| Atomic | Writes go through `paths.atomic_write_bytes`. | persistence (A) |
| Resilient read | Reads go through `paths.read_json_dict` (handles missing/parse-error/wrong-shape JSON). | persistence (B) |
| Permission | Approvals file is `0o600`; parent dir `0o700`. | persistence (E) |

## Static guards

**F1 — No direct approval-store access outside `approvals.py`**

Scans the package for any reference to the literal `"approvals.json"` string outside `approvals.py`. Direct reads or writes bypass the SentinelLock, the scope normalization, and the expiration filter. `doctor.py` is allow-listed: it only inspects file permission bits via `stat()`, never the contents.

**F2 — User-tab handlers route through the approvals layer**

AST walk over `read_user_tabs.py` confirms that every fetch handler (`_resolve_and_authorize_tab`, `list_tabs`) calls at least one of:

- `can_read_url(...)` — per-URL authorization
- `is_default_denied(...)` — direct default-deny check
- `list_active_scopes(...)` — meta-permission via sentinel-scope check

A new user-tab handler added without ANY approval-layer call trips this test instead of silently fetching authenticated content.

**F3 — Canonical authorization functions are pure (no I/O)**

`normalize_scope`, `approval_matches`, `can_read_url` are pure functions of their arguments. They do NOT read or write the store. Required for defense-in-depth: corrupt store records can't crash the decision functions. Verified by source inspection (`open(`, `atomic_write_bytes(`, `read_text(`, `json.loads(` etc. banned in those function bodies).

## `can_read_url` precedence (canonical)

```
if any exact_one_time scope matches url:
    return True                          # exact override wins
if is_default_denied(url):
    return False                         # default-deny blocks hostname/wildcard
if any persistent scope matches url:
    return True
return False                             # default state: DENY
```

Three steps, deterministic, ordered. Any new layer (e.g., a "trusted
agent" tier) MUST insert at a documented position and update this
table.

## Maintenance

**Adding a new scope kind**:

1. Add to `VALID_SCOPE_KINDS` in approvals.py.
2. Add a per-kind canonicalizer in `normalize_scope`.
3. Document grammar + canonical form in this file.
4. Add A-section tests for the new kind's grammar.
5. Run the suite — A6 idempotence test exercises round-trip stability.

**Adding a new fetch handler that reads user-authenticated content**:

1. Call `can_read_url(url, list_active_scopes(session_id=...), exact_one_time=...)` before fetching.
2. Add the handler name to `REQUIRED_GATES` in
   `test_f2_user_tab_handlers_route_through_can_read_url`.
3. Run the suite — F2 will fail-loud if the handler doesn't route
   through the approvals layer.

**Adding a new module that needs to inspect the approvals file
metadata** (perm/mtime/size, NOT content):

1. Add to `METADATA_ONLY_ALLOW_LIST` in F1 with a justification.
2. Use `path.exists()` / `path.stat()` only — never `path.read_text()`
   or `read_json_dict()`.

**Removing or relaxing an invariant** is a security boundary change
— code review with explicit threat-model discussion. Update this
doc in the same commit.
