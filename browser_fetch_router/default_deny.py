from __future__ import annotations

from fnmatch import fnmatch
from urllib.parse import urlsplit

# Hostnames that are sensitive even before considering the path. Listing one of
# these as the visible host of a tab leaks the fact that the user has the site
# open. read-user-tabs must redact these from list output.
#
# Every entry that has subdomains in real use also gets a `*.` wildcard so
# `www.gmail.com`, `m.outlook.live.com`, `us-east-1.console.aws.amazon.com`,
# etc. don't bypass the deny via a subdomain the bare entry doesn't match
# (the matcher does exact equality for non-wildcard entries — this was the
# round-3 finding U bypass; round-15 r15-02 found the invariant violated
# again for `console.aws.amazon.com` / `console.cloud.google.com` /
# `mail.google.com`). The contract test
# `tests/.../test_round15_replication.py::test_r15_02_every_bare_hostname_has_wildcard_counterpart`
# enforces the invariant: any future bare-hostname addition without its
# `*.<host>` sibling fails the build, so the regional-subdomain bypass
# cannot quietly re-enter through a third route.
HOST_SENSITIVE_PATTERNS = [
    "1password.com",
    "*.1password.com",
    "lastpass.com",
    "*.lastpass.com",
    "bitwarden.com",
    "*.bitwarden.com",
    "gmail.com",
    "*.gmail.com",
    "mail.google.com",
    "*.mail.google.com",
    "outlook.live.com",
    "*.outlook.live.com",
    "outlook.office.com",
    "*.outlook.office.com",
    "mail.yahoo.com",
    "*.mail.yahoo.com",
    # AWS Console: bare host + `*.` wildcard captures regional consoles
    # like `us-east-1.console.aws.amazon.com`. Without the wildcard the
    # exact-equality matcher silently lets every regional IAM/EC2 tab
    # bypass the deny gate (Greptile P1-security r15-02 on 201a050).
    "console.aws.amazon.com",
    "*.console.aws.amazon.com",
    # GCP Console: same defensive shape — the bare entry doesn't catch
    # any future per-project subdomain Google may introduce.
    "console.cloud.google.com",
    "*.console.cloud.google.com",
    "portal.azure.com",
    "*.portal.azure.com",
    "id.me",
    "*.id.me",
]

# Path-sensitive patterns: hostname is OK to mention but specific subpaths are
# default-denied (e.g., /settings on a public site).
PATH_SENSITIVE_PATTERNS = [
    "github.com/settings/*",
    "github.com/organizations/*/settings/*",
    "github.com/account/*",
    "gitlab.com/-/profile/*",
    "gitlab.com/-/account/*",
    "billing.stripe.com/*",
    "dashboard.stripe.com/*",
    "paypal.com/*",
    "stripe.com/*",
]

DENY_PATTERNS = [
    "1password.com/*",
    "*.1password.com/*",
    "lastpass.com/*",
    "*.lastpass.com/*",
    "bitwarden.com/*",
    "*.bitwarden.com/*",
    "mail.google.com/*",
    "*.mail.google.com/*",
    "gmail.com/*",
    "*.gmail.com/*",
    "outlook.live.com/*",
    "*.outlook.live.com/*",
    "outlook.office.com/*",
    "*.outlook.office.com/*",
    "mail.yahoo.com/*",
    "*.mail.yahoo.com/*",
    # AWS / GCP consoles — bare + wildcard so regional/per-project
    # subdomains (e.g. `us-east-1.console.aws.amazon.com`) are blocked
    # by the URL-deny path, not just the host-sensitive list (Greptile
    # P1-security r15-02 on 201a050). The static-guard test
    # `test_r15_02_every_bare_hostname_has_wildcard_counterpart`
    # locks the invariant in: future bare-hostname additions trip the
    # build if their `*.<host>/*` counterpart is missing.
    "console.aws.amazon.com/*",
    "*.console.aws.amazon.com/*",
    "console.cloud.google.com/*",
    "*.console.cloud.google.com/*",
    "portal.azure.com/*",
    "*.portal.azure.com/*",
    "id.me/*",
    "*.id.me/*",
    *PATH_SENSITIVE_PATTERNS,
]


def is_default_denied(url: str) -> bool:
    """Return True if this URL must never be read without explicit per-URL
    approval — even if a hostname/wildcard approval is on file."""
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path or "/"
    if is_default_denied_hostname(host):
        return True
    return any(_url_pattern_matches(pattern, host, path) for pattern in DENY_PATTERNS)


def is_default_denied_hostname(host: str) -> bool:
    """Return True if just exposing the hostname in a tab list is sensitive."""
    host = host.lower()
    return any(_hostname_matches(pattern, host) for pattern in HOST_SENSITIVE_PATTERNS)


def _url_pattern_matches(pattern: str, host: str, path: str) -> bool:
    pat_host, sep, pat_path = pattern.partition("/")
    if not _hostname_matches(pat_host, host):
        return False
    if not sep:
        return True
    return fnmatch(path, "/" + pat_path)


def _hostname_matches(pattern: str, host: str) -> bool:
    if pattern.startswith("*."):
        parent = pattern[2:]
        return host == parent or host.endswith("." + parent)
    return host == pattern
