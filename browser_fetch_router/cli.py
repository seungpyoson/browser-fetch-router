from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence

from browser_fetch_router.audit import append_audit
from browser_fetch_router.schema import envelope, schema_payload
from browser_fetch_router.status import STATUS_EXIT_CODES
from browser_fetch_router.url_safety import SafetyError

ALIAS_COMMANDS = {"read-web", "read-user-tabs", "interactive-browser"}


def _emit(
    command: str,
    *,
    url: str | None = None,
    task: str | None = None,
    handler,
    exit_code_fn=None,
    audit: bool = True,
) -> int:
    """Run `handler`, print a structured payload, audit, return a documented exit code.

    Single serialization site — every code path (success, SafetyError,
    Exception, KeyboardInterrupt, SystemExit) lands at the same
    `print + audit + exit-code` block, so the contract "every invocation
    produces an envelope and an audit entry" cannot be violated by a
    handler returning a non-serializable payload, by SIGINT mid-action,
    or by a future maintainer adding an exception-handler branch that
    forgets to call audit.

    Outcome buckets:

    - SafetyError → `unsafe_url_blocked` envelope, exit 4.
    - KeyboardInterrupt / SystemExit → `interrupted` envelope, exit 130.
      Audit fires BEFORE the process exits so the forensic record of "user
      cancelled mid-action" is durable; an attacker SIGINT-ing immediately
      after a side effect committed cannot suppress the entry.
    - Any other Exception → `internal_error` envelope, exit 70.
    - Success → handler's envelope passed through unchanged.

    `exit_code_fn` overrides the default `STATUS_EXIT_CODES` lookup for
    handlers whose payload is not envelope-shaped (schema, test-acceptance).
    `audit=False` skips audit emission for purely informational commands
    (schema, test-acceptance) — operational commands always audit.
    """
    # Lazy import: keeps approvals/lifecycle out of the cli import graph
    # at module load time so a future maintainer adding a top-level cli
    # import to either doesn't introduce a circular dependency.
    from browser_fetch_router.approvals import InvalidScope
    from browser_fetch_router.lifecycle import InvalidSessionId

    interrupted = False
    try:
        payload = handler()
    except SafetyError as exc:
        payload = envelope(
            command=command,
            status="unsafe_url_blocked",
            url=url,
            error={"code": str(exc), "message": "URL blocked by safety policy"},
        )
    except InvalidScope as exc:
        # Malformed `--approval-scope=...` / `revoke <scope>` argument.
        # Surfaced as usage_error (exit 2) instead of internal_error (exit
        # 70) so the user sees "you passed a bad scope" rather than a
        # generic crash. Without this branch InvalidScope (a ValueError
        # subclass) hits the bare `except Exception` path below, masking
        # the real cause as `uncaught_exception`. Round-6 r6-02.
        payload = envelope(
            command=command,
            status="usage_error",
            url=url,
            error={
                "code": "invalid_approval_scope",
                "message": str(exc),
                "type": type(exc).__name__,
            },
        )
    except InvalidSessionId as exc:
        # Caller-supplied session_id (BFR_SESSION_ID, --session-id) failed
        # the grammar / containment check. Surfaced as usage_error so the
        # user sees the rejected ID. Critical: lands here BEFORE the
        # generic `except Exception` path so a path-traversal attempt on
        # the session registry never silently degrades to a no-op
        # internal_error envelope. Round-6 r6-05.
        payload = envelope(
            command=command,
            status="usage_error",
            url=url,
            error={
                "code": "invalid_session_id",
                "message": str(exc),
                "type": type(exc).__name__,
            },
        )
    except KeyboardInterrupt:
        payload = envelope(
            command=command,
            status="interrupted",
            url=url,
            error={
                "code": "user_interrupt",
                "message": "Operation cancelled by SIGINT (Ctrl-C)",
            },
        )
        interrupted = True
    except SystemExit as exc:
        # Convert to envelope so the forensic record exists. The exit code
        # falls through to STATUS_EXIT_CODES["interrupted"] = 130; if a
        # caller deliberately needs a specific exit code they should return
        # an envelope with a status that maps to it, not raise SystemExit.
        payload = envelope(
            command=command,
            status="interrupted",
            url=url,
            error={
                "code": "system_exit",
                "message": f"SystemExit raised by handler (code={exc.code!r})",
            },
        )
        interrupted = True
    except Exception as exc:
        payload = envelope(
            command=command,
            status="internal_error",
            url=url,
            error={
                "code": "uncaught_exception",
                "message": str(exc)[:200],
                "type": type(exc).__name__,
            },
        )

    # Single serialization site — guarded so a non-JSON-serializable
    # handler payload (e.g., evidence containing a function or Path object)
    # downgrades to `internal_error` instead of crashing the CLI.
    serialized, payload = _serialize_or_internal_error(payload, command, url)
    print(serialized)
    if audit:
        _emit_audit(command, url=url, task=task, payload=payload)
    if interrupted:
        # We've audited and emitted the envelope. Exit cleanly with the
        # POSIX SIGINT code instead of letting KeyboardInterrupt propagate
        # (which would print a traceback to stderr).
        return _exit_code_for_payload(payload)
    if exit_code_fn is not None:
        return exit_code_fn(payload)
    return _exit_code_for_payload(payload)


def _exit_code_for_payload(payload: dict) -> int:
    return STATUS_EXIT_CODES.get(
        payload.get("status"),
        STATUS_EXIT_CODES["internal_error"],
    )


def _serialize_or_internal_error(
    payload: dict, command: str, url: str | None
) -> tuple[str, dict]:
    """Serialize `payload` to JSON, or fall back to an internal_error envelope.

    The fallback exists so a handler returning a non-JSON-serializable
    payload (a function, a Path, a custom object — anything `json.dumps`
    can't handle) cannot crash the CLI. Returns (serialized, payload) so
    the caller can audit the actually-emitted payload, not the failed one.
    """
    try:
        return json.dumps(payload, sort_keys=True), payload
    except (TypeError, ValueError) as exc:
        fallback = envelope(
            command=command,
            status="internal_error",
            url=url,
            error={
                "code": "non_serializable_payload",
                "message": str(exc)[:200],
                "type": type(exc).__name__,
            },
        )
        return json.dumps(fallback, sort_keys=True), fallback


def _emit_audit(
    command: str,
    *,
    url: str | None,
    task: str | None,
    payload: dict,
) -> None:
    """Single audit-write site for the CLI dispatcher.

    Reads handler-stamped fields (route, cached, session_id,
    invoking_agent) from the returned envelope when present, with a
    fallback to the session module so commands that don't stamp evidence
    (read-user-tabs, interactive-browser, cleanup, install-agent, doctor)
    still get session_id and invoking_agent attribution. Wrapped in a
    BaseException-broad except so any audit-write failure — including
    SIGINT (KeyboardInterrupt) or MemoryError mid-write_all — is
    non-fatal. Audit is a best-effort durability layer, not a
    request-blocking gate; the user's command envelope was already
    committed to stdout before this call, so an exception escaping here
    would (a) print an uncaught traceback to stderr after the JSON line
    and (b) corrupt the CLI's exit code (130 → 1). Round-6 r6-04 closes
    that class.
    """
    evidence = payload.get("evidence") if isinstance(payload, dict) else None
    if not isinstance(evidence, dict):
        evidence = {}
    # Lazy import: keeps `cli` importable even if the session module is
    # being patched in a test. The session helpers read from env vars and
    # are cheap.
    from browser_fetch_router.session import current_session_id, invoking_agent

    try:
        append_audit({
            "command": command,
            "input_url_or_task": url or task or "",
            "status": payload.get("status") if isinstance(payload, dict) else None,
            "route": payload.get("route") if isinstance(payload, dict) else None,
            "session_id": evidence.get("session_id") or current_session_id(optional=True),
            "invoking_agent": evidence.get("invoking_agent") or invoking_agent(),
            "cached": evidence.get("cached"),
        })
    except BaseException:  # noqa: BLE001
        # The full BaseException catch is intentional. We MUST also
        # absorb KeyboardInterrupt and MemoryError here: SIGINT during
        # the write_all loop in append_audit() would otherwise propagate
        # past _emit's KeyboardInterrupt handler (which only wraps
        # `handler()`, not the audit call), causing a traceback after a
        # successful envelope was already on stdout. SystemExit is also
        # caught — handlers that raise SystemExit are normalized in
        # _emit; an audit-time SystemExit is anomalous and we want it
        # absorbed too. The audit JSONL file may end up with a partial
        # line on disk; downstream consumers must skip lines that fail
        # json.loads (a property already documented for forensic tools).
        pass


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        # argparse error happens BEFORE we know which command dispatched,
        # so we can't route through _emit (which needs a handler). Audit
        # the attempt directly so probes for available commands / typo'd
        # invocations leave a forensic trail.
        payload = envelope(
            command="unknown",
            status="usage_error",
            error={"code": "usage_error", "message": message},
        )
        print(json.dumps(payload, sort_keys=True))
        _emit_audit("unknown", url=None, task=None, payload=payload)
        raise SystemExit(STATUS_EXIT_CODES["usage_error"])


def _usage_error(command: str, message: str) -> int:
    payload = envelope(
        command=command,
        status="usage_error",
        error={"code": "usage_error", "message": message},
    )
    print(json.dumps(payload, sort_keys=True))
    _emit_audit(command, url=None, task=None, payload=payload)
    return STATUS_EXIT_CODES["usage_error"]


def build_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(prog="browser-fetch-router")
    sub = parser.add_subparsers(dest="command", required=True)

    read_web = sub.add_parser(
        "read-web",
        description=(
            "Fetch a public web URL through the shared router. Paid Parallel "
            "fallback runs only with --allow-paid and PARALLEL_API_KEY."
        ),
    )
    read_web.add_argument("url")
    read_web.add_argument("--json", action="store_true")
    read_web.add_argument("--no-cache", action="store_true")
    read_web.add_argument(
        "--allow-paid",
        action="store_true",
        help="Allow paid Parallel fallback when the free/public route is insufficient",
    )
    read_web.add_argument("--strict-side-effects", action="store_true")
    read_web.add_argument("--allow-side-effects", action="store_true")
    read_web.add_argument("--max-chars", type=int, default=50_000)

    cdp_setup_help = (
        "Uses loopback Chrome CDP at http://127.0.0.1:9222. Start Chrome/Chromium "
        "with --remote-debugging-address=127.0.0.1 --remote-debugging-port=9222 "
        "--user-data-dir=<temporary-profile>; do not use the normal profile. "
        "--allow-remote-cdp is an explicit override for non-loopback endpoints."
    )
    tabs = sub.add_parser("read-user-tabs", description=cdp_setup_help)
    tabs_sub = tabs.add_subparsers(dest="tabs_command", required=True)
    tabs_list = tabs_sub.add_parser("list", description=cdp_setup_help)
    tabs_list.add_argument("--json", action="store_true")
    tabs_list.add_argument("--all", action="store_true")
    tabs_list.add_argument("--show-all", action="store_true")
    tabs_list.add_argument("--allow-remote-cdp", action="store_true")
    # Single-command authorization parity with `read` / `screenshot`:
    # `--approval-scope=exact:list-all-tabs --persist-approval --all`
    # grants the broad-listing scope and runs the listing in one
    # invocation (Gemini medium on commit 3b131b7).
    tabs_list.add_argument("--approval-scope")
    tabs_list.add_argument("--persist-approval", action="store_true")
    tabs_read = tabs_sub.add_parser("read", description=cdp_setup_help)
    tabs_read.add_argument("target")
    tabs_read.add_argument("--json", action="store_true")
    tabs_read.add_argument("--approval-scope")
    tabs_read.add_argument("--persist-approval", action="store_true")
    tabs_read.add_argument("--allow-remote-cdp", action="store_true")
    tabs_read.add_argument("--max-chars", type=int, default=20_000)
    tabs_shot = tabs_sub.add_parser("screenshot", description=cdp_setup_help)
    tabs_shot.add_argument("target")
    tabs_shot.add_argument("--output", required=True)
    tabs_shot.add_argument("--json", action="store_true")
    tabs_shot.add_argument("--approval-scope")
    tabs_shot.add_argument("--persist-approval", action="store_true")
    tabs_shot.add_argument("--allow-remote-cdp", action="store_true")
    tabs_setup = tabs_sub.add_parser("setup", description=cdp_setup_help)
    tabs_setup.add_argument("--json", action="store_true")
    tabs_setup.add_argument(
        "--launch",
        action="store_true",
        help="Start a temporary loopback Chrome CDP profile",
    )
    tabs_setup.add_argument(
        "--start-url",
        default="about:blank",
        help="Initial URL for --launch; http(s) only, or about:blank",
    )
    tabs_revoke = tabs_sub.add_parser("revoke")
    tabs_revoke.add_argument("scope")
    tabs_revoke.add_argument("--json", action="store_true")

    browser = sub.add_parser(
        "interactive-browser",
        description=(
            "Interactive browser task runner. Provider capability truth: "
            "cloud=live with BROWSER_USE_API_KEY and --allow-hosted-browser; "
            "browserbase=live with BROWSERBASE_API_KEY and --allow-hosted-browser; "
            "optional BROWSERBASE_PROJECT_ID is passed through when present."
        ),
    )
    browser.add_argument("task")
    browser.add_argument("--json", action="store_true")
    browser.add_argument(
        "--provider",
        choices=["browserbase", "cloud"],
        help=(
            "cloud=live with BROWSER_USE_API_KEY; "
            "browserbase=live with BROWSERBASE_API_KEY and optional BROWSERBASE_PROJECT_ID"
        ),
    )
    browser.add_argument("--allow-hosted-browser", action="store_true")
    browser.add_argument("--confirm-irreversible")
    browser.add_argument(
        "--max-steps",
        type=int,
        default=10,
        help="Maximum task steps; cloud sessions poll provider stepCount and stop at the cap",
    )
    browser.add_argument("--max-duration-sec", type=int, default=300)
    browser.add_argument(
        "--max-cost-usd",
        type=float,
        default=0.25,
        help=(
            "Per-call and per-session hosted-browser cap; "
            "daily cap uses BFR_HOSTED_BROWSER_DAILY_COST_CAP_USD"
        ),
    )

    doctor = sub.add_parser("doctor")
    doctor.add_argument("--global-install", action="store_true")
    doctor.add_argument("--json", action="store_true")

    cleanup = sub.add_parser("cleanup")
    cleanup.add_argument("--all", action="store_true")
    cleanup.add_argument("--global-orphan-reap", action="store_true")
    cleanup.add_argument("--logs", action="store_true")
    cleanup.add_argument("--max-age-days", type=int, default=30)
    cleanup.add_argument("--json", action="store_true")

    schema = sub.add_parser("schema")
    schema.add_argument("--json", action="store_true")

    acceptance = sub.add_parser("test-acceptance")
    acceptance.add_argument("--json", action="store_true")
    acceptance.add_argument("--include-network", action="store_true")
    acceptance.add_argument("--include-paid", action="store_true")

    install = sub.add_parser(
        "install-agent",
        description="Install thin browser-fetch-router agent adapter skills.",
    )
    from browser_fetch_router.install_agent import AGENTS

    install.add_argument(
        "agent",
        nargs="?",
        choices=AGENTS,
        help="Explicit supported agent to install.",
    )
    install_mode = install.add_mutually_exclusive_group()
    install_mode.add_argument(
        "--all",
        action="store_true",
        help="Install default agents and report explicit-only agents as skipped.",
    )
    install_mode.add_argument(
        "--select",
        help="Comma-separated subset of supported agents to install.",
    )
    install.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing adapter.",
    )
    install.add_argument(
        "--adapter-path",
        help="Explicit destination file path; basename must be SKILL.md.",
    )
    install.add_argument("--json", action="store_true")

    return parser


def normalize_argv(
    argv: Sequence[str] | None,
    *,
    invoked_as: str | None = None,
    process_args: Sequence[str] | None = None,
) -> list[str]:
    if argv is not None:
        return list(argv)
    invoked = os.path.basename(invoked_as or sys.argv[0])
    rest = list(process_args if process_args is not None else sys.argv[1:])
    if invoked in ALIAS_COMMANDS:
        return [invoked, *rest]
    return rest


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(normalize_argv(argv))

    if args.command == "schema":
        return _emit(
            "schema", handler=schema_payload, exit_code_fn=lambda _p: 0, audit=False
        )

    if args.command == "doctor":
        from browser_fetch_router.doctor import run_doctor
        return _emit("doctor", handler=lambda: run_doctor(global_install=args.global_install))

    if args.command == "read-web":
        from browser_fetch_router.read_web import read_web
        from browser_fetch_router.session import current_session_id, invoking_agent
        return _emit(
            "read-web",
            url=args.url,
            handler=lambda: read_web(
                args.url,
                allow_paid=args.allow_paid,
                no_cache=args.no_cache,
                strict_side_effects=args.strict_side_effects,
                allow_side_effects=args.allow_side_effects,
                max_chars=args.max_chars,
                session_id=current_session_id(),
                invoking_agent=invoking_agent(),
            ),
        )

    if args.command == "interactive-browser":
        from browser_fetch_router.interactive import run_interactive_browser
        return _emit(
            "interactive-browser",
            task=args.task,
            handler=lambda: run_interactive_browser(
                args.task,
                provider=args.provider,
                allow_hosted_browser=args.allow_hosted_browser,
                confirm_irreversible=args.confirm_irreversible,
                max_steps=args.max_steps,
                max_duration_sec=args.max_duration_sec,
                max_cost_usd=args.max_cost_usd,
            ),
        )

    if args.command == "test-acceptance":
        from browser_fetch_router.acceptance import run_acceptance
        return _emit(
            "test-acceptance",
            handler=lambda: run_acceptance(
                include_network=args.include_network,
                include_paid=args.include_paid,
            ),
            exit_code_fn=lambda p: 0 if p.get("failed", 1) == 0 else 1,
            audit=False,
        )

    if args.command == "install-agent":
        from browser_fetch_router.install_agent import AGENTS, install_agent, install_agents
        if args.all or args.select:
            if args.agent:
                return _usage_error(
                    "install-agent",
                    "agent is mutually exclusive with --all/--select",
                )
            if args.adapter_path:
                return _usage_error(
                    "install-agent",
                    "--adapter-path cannot be combined with --all or --select",
                )
            if args.all:
                selected_agents = AGENTS
            else:
                selected_agents = [a.strip() for a in args.select.split(",") if a.strip()]
                invalid = [a for a in selected_agents if a not in AGENTS]
                if not selected_agents:
                    return _usage_error("install-agent", "--select requires at least one agent")
                if invalid:
                    return _usage_error(
                        "install-agent",
                        f"unknown agent(s) in --select: {', '.join(invalid)}",
                    )
            return _emit(
                "install-agent",
                handler=lambda: install_agents(
                    selected_agents,
                    force=args.force,
                    default_mode=args.all,
                ),
            )
        if not args.agent:
            return _usage_error(
                "install-agent",
                "one of agent, --all, or --select is required",
            )
        return _emit(
            "install-agent",
            handler=lambda: install_agent(
                args.agent, force=args.force, adapter_path=args.adapter_path
            ),
        )

    if args.command == "cleanup":
        from browser_fetch_router.lifecycle import run_cleanup
        from browser_fetch_router.session import current_session_id
        return _emit(
            "cleanup",
            handler=lambda: run_cleanup(
                all_sessions=args.all,
                global_orphan_reap=args.global_orphan_reap,
                logs=args.logs,
                max_age_days=args.max_age_days,
                session_id=current_session_id(optional=True),
            ),
        )

    if args.command == "read-user-tabs":
        return _emit(
            "read-user-tabs",
            url=getattr(args, "target", None),
            handler=lambda: _dispatch_read_user_tabs(args),
        )

    return _emit(
        args.command,
        handler=lambda: envelope(
            command=args.command,
            status="tool_setup_failed",
            error={
                "code": "dispatch_missing",
                "message": f"{args.command} has no CLI dispatch",
            },
        ),
    )


def _dispatch_read_user_tabs(args) -> dict:
    # Import `revoke` (the envelope wrapper) — NOT `revoke_scope` (the
    # low-level approval-store helper, which returns a non-envelope dict and
    # would KeyError on STATUS_EXIT_CODES lookup).
    from browser_fetch_router.read_user_tabs import (
        list_tabs,
        read_tab,
        revoke,
        screenshot_tab,
        setup_cdp,
    )
    from browser_fetch_router.session import current_session_id

    if args.tabs_command == "list":
        return list_tabs(
            all_tabs=args.all,
            show_all=args.show_all,
            allow_remote_cdp=args.allow_remote_cdp,
            session_id=current_session_id(optional=True),
            approval_scope=args.approval_scope,
            persist_approval=args.persist_approval,
        )
    if args.tabs_command == "read":
        return read_tab(
            args.target,
            approval_scope=args.approval_scope,
            persist_approval=args.persist_approval,
            allow_remote_cdp=args.allow_remote_cdp,
            max_chars=args.max_chars,
            session_id=current_session_id(optional=True),
        )
    if args.tabs_command == "screenshot":
        return screenshot_tab(
            args.target,
            output=args.output,
            approval_scope=args.approval_scope,
            persist_approval=args.persist_approval,
            allow_remote_cdp=args.allow_remote_cdp,
            session_id=current_session_id(optional=True),
        )
    if args.tabs_command == "setup":
        return setup_cdp(launch=args.launch, start_url=args.start_url)
    if args.tabs_command == "revoke":
        return revoke(args.scope, session_id=current_session_id(optional=True))
    return envelope(
        command="read-user-tabs",
        status="usage_error",
        error={"code": "unknown_subcommand", "message": str(args.tabs_command)},
    )
