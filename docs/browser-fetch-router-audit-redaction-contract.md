# browser-fetch-router audit redaction contract

This document is the **single source of truth** for "safe forensic
logging" in the `browser_fetch_router` package. Every event written to
`audit.jsonl` MUST pass through `audit.sanitize_audit_input` for any
URL-or-task field, AND `audit.SECRET_TEXT_PATTERNS` MUST cover every
credential format an agent might inadvertently include in a free-form
task string. This file enumerates the invariants and the corpus that
drives pattern completeness.

The companion file
`tests/browser_fetch_router/test_round17_replication.py` contains the
canonical token corpus
(`MODERN_SECRET_TOKENS`); `test_audit.py` covers the legacy patterns.
A new credential format added to the corpus that ISN'T caught by some
pattern fails the build.

## Why this exists

PR #737's round-17 external review surfaced four-of-four reviewer
agreement that `SECRET_TEXT_PATTERNS` was missing modern token
formats — Stripe `sk_live_/sk_test_`, GitHub fine-grained
`github_pat_`, GitLab `glpat-`, Sendgrid `SG.`, Google `AIza`,
Twilio `SK/AC`. The legacy set covered Bearer / OpenAI `sk-` / Slack
xox / JWT / AWS AKIA / classic GitHub `ghp_` `ghs_` only. An audit
input containing a Stripe live key emitted the plaintext value into
the 0o600 forensic log.

Class A round-17 added the missing patterns AND established a
corpus-driven discipline: the corpus IS the contract. New formats are
added by APPENDING to `MODERN_SECRET_TOKENS` and adding a matching
pattern; the parametrized test then enforces redaction for every
entry.

## Threat model

Audit redaction defends against:

- **Plaintext credential leakage in forensic logs.** An agent
  accidentally includes a credential in an interactive-browser task
  string (`"pay with sk_live_..."`); without redaction, the secret
  persists in `~/.local/state/browser-fetch-router/audit.jsonl` (mode
  0o600 but durable forensic record). Local-machine attackers,
  forensic recovery from disposed disks, and accidental sharing of
  the audit log all expose the plaintext.
- **OAuth token leakage in URL fragments.** Implicit-flow OAuth
  callbacks land bearer tokens in `#access_token=...` (RFC 6749
  §4.2.2). The wire never sees the fragment, but the agent's input
  URL does — without fragment-key redaction, the token is logged.
- **Sensitive-key query-parameter leakage.** OAuth code, signed-URL
  signature, session tokens in query strings — same pattern, redact
  the value while preserving the key name as a forensic signal.

Out of scope:

- **Mutating cookies / session state.** `read-web` is cookieless;
  `read-user-tabs` reads the user's authenticated browser via CDP but
  does not log cookie contents (only page content, capped).
- **Active key revocation.** Detecting a leaked key in the audit log
  is an operator concern; the contract redacts at WRITE time so the
  log never contains the plaintext in the first place.
- **Metadata about WHICH credential leaked.** Patterns redact the
  value but the prefix (e.g. `sk_live_`) survives in the
  `[redacted]` substitution because the regex matches the WHOLE
  token. If you need "leaked-Stripe-key" forensic signal, add a
  prefix-preserving pattern variant — currently we trade off
  forensic specificity for simpler completeness.

## Invariants

| ID | Invariant | Why | Verified at |
|---|---|---|---|
| **R1** | Every URL-or-task event field passes through `sanitize_audit_input` before reaching `append_durable_line` | Single chokepoint — bypass = leak | `audit.append_audit:99-100` |
| **R2** | `SECRET_TEXT_PATTERNS` redacts every entry in the corpus (Bearer, OpenAI sk-, Slack xox, JWT, AWS AKIA, GitHub gh{psoru}_, GitHub fine-grained, Stripe live/test, GitLab, Sendgrid, Google AIza, Twilio SK/AC) | Corpus IS the contract — adding a new format means adding to BOTH the corpus and the patterns | `tests/.../test_round17_replication.py::MODERN_SECRET_TOKENS` + `test_audit.py` |
| **R3** | URL-shape detection (scheme + netloc) gates URL-style redaction so free-form task strings (interactive-browser) aren't mis-parsed by `urlsplit` | Without the gate, `urlunsplit` percent-encodes parts of a sentence as if it were a URL path | `audit.sanitize_audit_input:65-66` |
| **R4** | Sensitive query keys (`token`, `signature`, `code`, `state`, `access_token`, etc.) redact the VALUE; the KEY survives so the forensic record shows "an access_token was here" | Forensic signal preserved without leaking secret | `audit.SENSITIVE_QUERY_KEYS` |
| **R5** | Fragment redaction matches the query-key set (OAuth implicit-flow lands tokens in `#`); fragment treated as parameter-shaped only when it contains `=` | Without fragment redaction, implicit-flow tokens land in audit | `audit.sanitize_audit_input:79-83` |
| **R6** | Append-side guarantees (atomic line write, fsync durability, 0o600 mode) come from `paths.append_durable_line` — audit MUST route through it, not inline `os.open(O_APPEND)` | Cross-subsystem invariant — covered by persistence contract C/D/E | persistence contract; static guard `test_no_adhoc_persistent_writes` |

## Token corpus (round-17 baseline)

Each entry is a synthetic token whose prefix/length/charset matches
the public spec for that format. Real keys are NOT used in tests.
Adding a new format MUST add an entry here AND a pattern below.

| Format | Sample (synthetic) | Pattern |
|---|---|---|
| Bearer | `Bearer xxxxx` | `Bearer\s+\S+` (case-insensitive) |
| OpenAI | `sk-...` (≥20 chars) | `sk-[A-Za-z0-9_\-]{20,}` |
| Slack | `xoxb-...`, `xoxp-...`, `xoxa-...`, `xoxr-...`, `xoxs-...` | `xox[bpars]-[A-Za-z0-9-]+` |
| JWT | `eyJ.A.B` | `eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+` |
| AWS access key | `AKIA<16 upper>` | `AKIA[0-9A-Z]{16}` |
| GitHub PAT family | `ghp_<36>` `ghs_<36>` `gho_<36>` `ghr_<36>` `ghu_<36>` | `gh[psoru]_[A-Za-z0-9]{36}` |
| Stripe | `sk_live_<24+>` `sk_test_<24+>` | `sk_(?:live|test)_[A-Za-z0-9_]{24,}` |
| GitHub fine-grained PAT | `github_pat_<22+>` | `github_pat_[A-Za-z0-9_]{22,}` |
| GitLab | `glpat-<20+>` | `glpat-[A-Za-z0-9_\-]{20,}` |
| Sendgrid | `SG.<22+>.<43+>` | `SG\.[A-Za-z0-9_\-]{22,}\.[A-Za-z0-9_\-]{43,}` |
| Google API key | `AIza<35>` | `AIza[A-Za-z0-9_\-]{35}` |
| Twilio | `SK<32 hex>` `AC<32 hex>` | `(?:SK|AC)[0-9a-fA-F]{32}` |

## Maintenance

**Adding a new credential format**:

1. Append a `(format-name, sample-token)` pair to `MODERN_SECRET_TOKENS`
   in `tests/browser_fetch_router/test_round17_replication.py`. Use a
   synthetic token whose prefix/length/charset match the published
   format. Real keys are NEVER used.
2. Add a `re.compile(...)` entry to `SECRET_TEXT_PATTERNS` in
   `audit.py`. Match the published spec exactly — too-broad regexes
   false-positive on harmless strings; too-narrow ones miss real
   tokens.
3. Add a row to the **Token corpus** table above with the format
   name, sample shape, and the regex.
4. Run the contract suite — the parametrized test
   `test_class_a_modern_secret_formats_redacted` now exercises the
   new format. The test fails until the pattern matches.

**Adding a new sensitive query key**:

1. Append the key (lowercase) to `SENSITIVE_QUERY_KEYS` in
   `audit.py`.
2. Add a behavioral test in `test_audit.py` confirming the key's
   value is redacted while the key name survives.

**Removing a pattern** is a forensics-erosion change — it MUST go
through code review with explicit justification (e.g., "the format
was deprecated by the vendor in 2025; no current keys match it"). Do
NOT remove patterns to silence false-positive alerts; tighten the
pattern instead.

## Known limitations

- **Acceptable false-positives.** Some patterns (notably the Twilio
  `(?:SK|AC)[0-9a-fA-F]{32}`) match any 32-char hex string with the
  matching uppercase prefix, including non-Twilio identifiers (a
  SHA-256 hash truncated to 32 chars and prefixed with `SK`). The
  trade-off is intentional: over-redaction is the safe failure mode
  for a forensic log. A real Twilio key getting through is worse than
  a SHA hash being redacted. Tightening with `\b` word boundaries
  could reduce false-positives but would also miss tokens embedded
  in URL paths or JSON values without surrounding word boundaries.
  F-17d (round-17 followup adversarial review).

## What this contract does NOT cover

- **Cookie/header redaction in HTTP responses.** That's covered by
  `http_client.py`'s response-cap + the cache schema, not by audit.
- **Audit-log rotation.** Operators handle external rotation of
  `audit.jsonl` (invariant H of the persistence contract).
  `doctor.py` warns at 100 MB.
- **Proactive credential scanning of cached HTML responses.** Cache
  entries store provider responses verbatim — if a public webpage
  embeds a leaked credential, the cache stores it. The audit log
  redacts the URL/task fields only; the body bytes are out of scope.
