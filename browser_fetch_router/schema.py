from __future__ import annotations

import json
from importlib import resources
from typing import Any

SCHEMA_VERSION = "browser-fetch-router.v1"


def envelope(command: str, status: str, **fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "command": command,
        "status": status,
        "url": fields.pop("url", None),
        "route": fields.pop("route", None),
        "provider": fields.pop("provider", None),
        "title": fields.pop("title", None),
        "content_markdown": fields.pop("content_markdown", None),
        "artifacts": fields.pop("artifacts", []),
        "quality": fields.pop("quality", None),
        "evidence": fields.pop("evidence", None),
        "approval": fields.pop("approval", {"required": False, "scope": None}),
        "next_path": fields.pop("next_path", None),
        "error": fields.pop("error", None),
    }
    base.update(fields)
    return base


def schema_payload() -> dict[str, Any]:
    text = resources.files("browser_fetch_router.schemas").joinpath("v1.json").read_text()
    output_schema = json.loads(text)
    from browser_fetch_router.install_agent import AGENTS

    install_schema = output_schema["commandFlags"]["install-agent"]
    install_schema["properties"]["agent"]["enum"] = list(AGENTS)
    install_schema["properties"]["--select"]["description"] = (
        "Comma-separated subset of supported agents: " + ",".join(AGENTS)
    )
    from browser_fetch_router.interactive import provider_capabilities

    output_schema["commandFlags"]["interactive-browser"]["providerCapabilities"] = (
        provider_capabilities()
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "output_schema": output_schema,
        "commands": [
            "read-web",
            "read-user-tabs",
            "interactive-browser",
            "doctor",
            "cleanup",
            "schema",
            "install-agent",
        ],
    }
