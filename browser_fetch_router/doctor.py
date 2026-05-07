from __future__ import annotations

import stat
from pathlib import Path

from browser_fetch_router.paths import cache_dir, config_dir, ensure_all_dirs, state_dir
from browser_fetch_router.schema import envelope


def _too_permissive(path: Path) -> bool:
    if not path.exists():
        return False
    mode = stat.S_IMODE(path.stat().st_mode)
    return bool(mode & 0o077)


def run_doctor() -> dict[str, object]:
    paths = ensure_all_dirs()
    checked = [
        state_dir(),
        config_dir(),
        state_dir() / "audit.jsonl",
        config_dir() / "approvals.json",
    ]
    bad = [str(path) for path in checked if _too_permissive(path)]
    if bad:
        return envelope(
            command="doctor",
            status="tool_setup_failed",
            error={"code": "insecure_permissions", "paths": bad},
            evidence={"paths": paths},
        )
    cache_warn: list[str] = []
    if cache_dir().exists():
        for entry in cache_dir().rglob("*"):
            if entry.is_file() and (stat.S_IMODE(entry.stat().st_mode) & 0o033):
                cache_warn.append(str(entry))
    payload = envelope(command="doctor", status="ok", evidence={"paths": paths})
    if cache_warn:
        payload.setdefault("warnings", []).append(
            {"code": "cache_too_permissive", "paths": cache_warn}
        )
    return payload
