from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from browser_fetch_router.paths import (
    append_durable_line,
    ensure_private_dir,
    state_dir,
)

SENSITIVE_QUERY_KEYS = {
    "token",
    "signature",
    "sig",
    "key",
    "api_key",
    "auth",
    "code",
    "state",
    "session",
    "access_token",
    "refresh_token",
    "id_token",
}
# Secret-text redaction patterns — class-level enumeration of cloud
# provider, version control, and AI/ML credential formats whose plaintext
# must never reach `audit.jsonl`. The token-corpus that DRIVES this set
# lives in `tests/browser_fetch_router/test_round17_replication.py`
# (`MODERN_SECRET_TOKENS`) and the older `tests/.../test_audit.py`
# fixtures. The class-level invariant: every prefix in the corpus must
# be redacted by at least one pattern below. The Class-A round-17 fix
# expanded the set from the legacy {Bearer, sk-, xox, JWT, AKIA, ghp_,
# ghs_} list to cover modern token formats that all four external
# reviewers (GPT, DeepSeek, GLM, Kimi) flagged as missing.
SECRET_TEXT_PATTERNS = [
    re.compile(r"Bearer\s+\S+", re.IGNORECASE),
    # OpenAI-style API key (`sk-...`). The trailing `-` distinguishes
    # this from Stripe (`sk_live_...` / `sk_test_...`) which have an
    # underscore in position 3 and need their own pattern below.
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"xox[bpars]-[A-Za-z0-9-]+"),
    re.compile(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    # GitHub: classic PAT (ghp_), server PAT (ghs_), OAuth (gho_), refresh
    # (ghr_), user-to-server (ghu_). Fixed-length 36-char body matches the
    # GitHub-published spec for non-fine-grained tokens.
    re.compile(r"gh[psoru]_[A-Za-z0-9]{36}"),
    # Stripe: live/test secret keys. Body charset is base62 + `_` per
    # Stripe's published format; minimum length 24 to avoid false
    # positives on words like `sk_REDACTED_TEST_FIXTURE`.
    re.compile(r"sk_(?:live|test)_[A-Za-z0-9_]{24,}"),
    # GitHub fine-grained PAT (released 2022). Distinct from the classic
    # ghp_ prefix; minimum body length 22 covers all observed real
    # tokens (real ones run ~80+ chars).
    re.compile(r"github_pat_[A-Za-z0-9_]{22,}"),
    # GitLab personal/project/group access tokens.
    re.compile(r"glpat-[A-Za-z0-9_\-]{20,}"),
    # Sendgrid API keys. Three dot-separated segments; per Sendgrid spec
    # segment 2 is 22 chars and segment 3 is 43 chars.
    re.compile(r"SG\.[A-Za-z0-9_\-]{22,}\.[A-Za-z0-9_\-]{43,}"),
    # Google API keys (Maps, Cloud, etc.). Body is fixed 35 chars.
    re.compile(r"AIza[A-Za-z0-9_\-]{35}"),
    # Twilio API key SID. Two prefix variants, each followed by 32 hex
    # chars. SK = API Key (most common); AC = Account SID (less
    # sensitive but still PII).
    re.compile(r"(?:SK|AC)[0-9a-fA-F]{32}"),
]

REDACTED = "[redacted]"


def sanitize_audit_input(value: str) -> str:
    """Redact secrets and — only when the input is URL-shaped — sensitive
    query AND fragment parameters.

    `event["input_url_or_task"]` carries either a URL (read-web,
    read-user-tabs) or a free-form natural-language task string
    (interactive-browser). URL-style redaction must NOT run on the
    free-form string: `urlsplit` happily treats the first `?` in a sentence
    as a query separator, and `urlunsplit` then percent-encodes the rest of
    the task description, mangling the audit-log entry. Detect URL-shape via
    presence of BOTH scheme and netloc; without those, only secret-text
    redaction applies and the value is returned as-is.

    Fragment redaction matters because OAuth implicit-flow callbacks land
    bearer tokens in `#access_token=...` (RFC 6749 §4.2.2). The wire never
    sees the fragment but the audit input does — without redaction, the
    plaintext token would persist in `audit.jsonl`. The forensic SIGNAL
    that a token-bearing fragment was present survives (the key name
    appears with `[redacted]` value); only the secret value is scrubbed.
    """
    redacted = value
    for pattern in SECRET_TEXT_PATTERNS:
        redacted = pattern.sub(REDACTED, redacted)
    parsed = urlsplit(redacted)
    if not (parsed.scheme and parsed.netloc):
        return redacted
    query = parsed.query
    fragment = parsed.fragment
    if not query and "=" not in fragment:
        return redacted
    if query:
        query = urlencode(
            [
                (k, v if k.lower() not in SENSITIVE_QUERY_KEYS else REDACTED)
                for k, v in parse_qsl(query, keep_blank_values=True)
            ]
        )
    if fragment and "=" in fragment:
        # Treat fragment as a parameter string only when it's
        # parameter-shaped (contains `=`). A bare `#section` anchor stays
        # untouched.
        fragment = urlencode(
            [
                (k, v if k.lower() not in SENSITIVE_QUERY_KEYS else REDACTED)
                for k, v in parse_qsl(fragment, keep_blank_values=True)
            ]
        )
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, query, fragment)
    )


def append_audit(event: dict[str, object]) -> str:
    audit_id = str(uuid.uuid4())
    event = dict(event)
    event["audit_id"] = audit_id
    event["timestamp"] = datetime.now(UTC).isoformat()
    if isinstance(event.get("input_url_or_task"), str):
        event["input_url_or_task"] = sanitize_audit_input(str(event["input_url_or_task"]))
    ensure_private_dir(state_dir())
    path = state_dir() / "audit.jsonl"
    line = (json.dumps(event, sort_keys=True) + "\n").encode("utf-8")
    # Routes through the package-wide append helper which enforces the
    # full append-log invariant set: atomic line append (flock +
    # write_all), crash-safe durability (fsync), 0o600 permission, and
    # graceful unlock-then-close ordering. Inlining these primitives
    # here previously omitted fsync — see persistence-contract
    # invariant C / r15-01.
    append_durable_line(path, line)
    return audit_id
