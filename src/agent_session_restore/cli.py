"""Command line interface for claude-session-restore (csr)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from agent_session_restore import __version__
from agent_session_restore.hooks import handle_session_end, handle_session_start
from agent_session_restore.launcher import SessionLauncher, TerminalBackend
from agent_session_restore.registry import SessionRegistry


def _render_table_plain(sessions: list[Any]) -> None:
    """Fallback plain ASCII table output."""
    if not sessions:
        print("No sessions recorded.")
        return

    header = f"{'AGENT':<12} {'STATUS':<8} {'NAME':<24} {'SESSION ID':<16} {'BRANCH':<14} {'DIRECTORY'}"
    print(header)
    print("-" * len(header) + "-" * 20)
    for s in sessions:
        agent_tag = s.agent.upper()
        status_text = "[ACTIVE]" if s.is_active else "[CLOSED]"
        short_id = s.session_id[:12]
        branch = s.git_branch or "-"
        name = s.name[:23]
        print(f"{agent_tag:<12} {status_text:<8} {name:<24} {short_id:<16} {branch:<14} {s.cwd}")


def _render_table_rich(sessions: list[Any]) -> None:
    """Rich visual table output if rich is installed."""
    try:
        from rich.console import Console
        from rich.table import Table

        console = Console()
        if not sessions:
            console.print("[dim]No sessions recorded.[/dim]")
            return

        table = Table(title=f"Multi-Agent Sessions ({len(sessions)} recorded)")
        table.add_column("Agent", style="bold cyan")
        table.add_column("Status", justify="center")
        table.add_column("Session Name", style="white", no_wrap=True)
        table.add_column("Session ID", style="magenta")
        table.add_column("Branch", style="green")
        table.add_column("Directory", style="yellow")
        table.add_column("Last Active", style="dim")

        for s in sessions:
            status_badge = "[green]● active[/green]" if s.is_active else "[dim]○ closed[/dim]"
            table.add_row(
                s.agent.upper(),
                status_badge,
                s.name,
                s.session_id[:12],
                s.git_branch or "-",
                s.cwd,
                s.updated_at[:19].replace("T", " "),
            )

        console.print(table)
    except ImportError:
        _render_table_plain(sessions)


def cmd_list(args: argparse.Namespace, registry: SessionRegistry) -> int:
    sessions = registry.list_sessions(active_only=not args.all, agent_filter=args.agent)
    if args.json:
        data = [s.to_dict() for s in sessions]
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return 0

    try:
        _render_table_rich(sessions)
    except Exception:
        _render_table_plain(sessions)
    return 0


def cmd_restore(args: argparse.Namespace, registry: SessionRegistry) -> int:
    sessions = registry.list_sessions(active_only=not args.all, agent_filter=args.agent)
    if not sessions:
        print("No sessions available to restore.")
        return 0

    backend: TerminalBackend = args.terminal
    launcher = SessionLauncher(backend=backend, delay_seconds=args.delay)

    if args.dry_run or backend == "print":
        print(f"\n--- Dry Run: Launch commands for {len(sessions)} sessions ---")
        for s in sessions[: args.limit] if args.limit else sessions:
            cmd = launcher.build_resume_command(s)
            print(f"[{s.agent.upper()}] [{s.name}] ({s.cwd}) -> {' '.join(cmd)}")
        return 0

    if args.generate_script:
        script_content = launcher.generate_powershell_script(
            sessions[: args.limit] if args.limit else sessions
        )
        out_file = Path(args.generate_script).resolve()
        out_file.write_text(script_content, encoding="utf-8")
        print(f"Generated PowerShell restore script: {out_file}")
        return 0

    print(f"Restoring {min(len(sessions), args.limit or len(sessions))} sessions using backend '{backend}'...")
    results = launcher.launch_many(sessions, limit=args.limit)

    success_count = sum(1 for r in results if r.success)
    fail_count = len(results) - success_count

    print(f"Done. Successfully launched: {success_count}, Failed: {fail_count}")
    for r in results:
        if not r.success:
            print(f"  [FAILED] [{r.session.agent.upper()}] {r.session.name}: {r.error_message}", file=sys.stderr)

    return 0 if fail_count == 0 else 1


def cmd_register(args: argparse.Namespace, registry: SessionRegistry) -> int:
    entry = registry.register(
        session_id=args.id,
        name=args.name,
        cwd=args.cwd,
        agent=args.agent,
        git_branch=args.branch,
        custom_resume_cmd=args.custom_cmd,
        status="active",
    )
    print(f"Registered [{entry.agent.upper()}] session '{entry.name}' ({entry.session_id}) in {entry.cwd}")
    return 0


def cmd_unregister(args: argparse.Namespace, registry: SessionRegistry) -> int:
    success = registry.unregister(args.id, hard_delete=args.hard)
    if success:
        action = "Deleted" if args.hard else "Closed"
        print(f"{action} session [{args.id}]")
        return 0
    else:
        print(f"Session [{args.id}] not found.", file=sys.stderr)
        return 1


def cmd_prune(args: argparse.Namespace, registry: SessionRegistry) -> int:
    count = registry.prune(
        max_age_days=args.days,
        keep_active=not args.all,
        remove_missing_cwd=args.missing_dirs,
    )
    print(f"Pruned {count} stale session(s).")
    return 0


def cmd_export(args: argparse.Namespace, registry: SessionRegistry) -> int:
    if args.format == "md":
        content = registry.export_markdown(active_only=not args.all, agent_filter=args.agent)
    else:
        sessions = registry.list_sessions(active_only=not args.all, agent_filter=args.agent)
        content = json.dumps([s.to_dict() for s in sessions], indent=2, ensure_ascii=False)

    if args.output:
        out_path = Path(args.output).resolve()
        out_path.write_text(content, encoding="utf-8")
        print(f"Exported sessions to {out_path}")
    else:
        print(content)
    return 0


def cmd_install_hooks(args: argparse.Namespace, registry: SessionRegistry | None = None) -> int:
    """Print configuration snippet for ~/.claude/settings.json."""
    script_dir = Path(__file__).resolve().parent.parent.parent / "hooks"
    start_hook = script_dir / "session-start.py"
    end_hook = script_dir / "session-end.py"

    config_snippet = {
        "hooks": {
            "SessionStart": [
                {
                    "matcher": "startup|clear|resume",
                    "command": f'python "{start_hook}"',
                }
            ],
            "SessionEnd": [
                {
                    "matcher": ".*",
                    "command": f'python "{end_hook}"',
                }
            ],
        }
    }

    print("\nAdd the following snippet to your ~/.claude/settings.json:\n")
    print(json.dumps(config_snippet, indent=2))
    print("\nOr point to `csr hook-start` and `csr hook-end` directly.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="asr",
        description="Fast multi-agent session registry & resume manager (Claude, Codex, Cursor, Antigravity, Hermes).",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--file",
        "-f",
        help="Custom path to session registry JSON file",
        default=None,
    )

    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # list
    p_list = subparsers.add_parser("list", help="List tracked agent sessions")
    p_list.add_argument("--all", "-a", action="store_true", help="Include closed sessions")
    p_list.add_argument("--agent", help="Filter by agent type (e.g. claude, codex, cursor, antigravity, hermes)")
    p_list.add_argument("--json", action="store_true", help="Output raw JSON format")

    # restore
    p_restore = subparsers.add_parser("restore", help="Fast resume sessions after OS restart")
    p_restore.add_argument(
        "--terminal",
        "-t",
        choices=["wt", "pwsh", "powershell", "cmd", "print"],
        default="wt" if SessionLauncher.is_windows_terminal_available() else "pwsh",
        help="Terminal backend to use (default: wt if Windows Terminal is installed, otherwise pwsh)",
    )
    p_restore.add_argument("--all", "-a", action="store_true", help="Restore all, including closed")
    p_restore.add_argument("--agent", help="Restore only specific agent sessions (e.g. codex, claude)")
    p_restore.add_argument("--limit", "-n", type=int, default=None, help="Limit number of sessions to restore")
    p_restore.add_argument(
        "--delay",
        type=float,
        default=0.3,
        help="Delay in seconds between launches (default: 0.3s)",
    )
    p_restore.add_argument("--dry-run", action="store_true", help="Print commands without launching")
    p_restore.add_argument(
        "--generate-script",
        type=str,
        default=None,
        help="Save launch commands into a standalone .ps1 script file",
    )

    # register
    p_reg = subparsers.add_parser("register", help="Register or update a session entry")
    p_reg.add_argument("--id", required=True, help="Session UUID or identifier")
    p_reg.add_argument("--name", help="Session semantic name")
    p_reg.add_argument("--cwd", help="Working directory (default: current directory)")
    p_reg.add_argument(
        "--agent",
        default="claude",
        help="Agent type: claude, codex, cursor, antigravity, hermes, custom (default: claude)",
    )
    p_reg.add_argument("--branch", help="Git branch name")
    p_reg.add_argument("--custom-cmd", help="Custom resume command template with {id}, {cwd}, {name}")

    # unregister
    p_unreg = subparsers.add_parser("unregister", help="Unregister or close a session entry")
    p_unreg.add_argument("--id", required=True, help="Session UUID")
    p_unreg.add_argument("--hard", action="store_true", help="Permanently delete from registry")

    # prune
    p_prune = subparsers.add_parser("prune", help="Clean up stale or deleted sessions")
    p_prune.add_argument("--days", type=int, default=14, help="Max age in days (default: 14)")
    p_prune.add_argument("--all", action="store_true", help="Include active sessions older than days")
    p_prune.add_argument(
        "--missing-dirs",
        action="store_true",
        help="Prune sessions whose cwd no longer exists",
    )

    # export
    p_exp = subparsers.add_parser("export", help="Export session registry as Markdown or JSON")
    p_exp.add_argument("--format", choices=["md", "json"], default="md", help="Export format")
    p_exp.add_argument("--all", action="store_true", help="Include closed sessions")
    p_exp.add_argument("--agent", help="Filter by agent type")
    p_exp.add_argument("--output", "-o", help="Target output file path")

    # install-hooks
    subparsers.add_parser("install-hooks", help="Display hook configuration for Claude Code")

    # hook-start
    subparsers.add_parser("hook-start", help="Direct SessionStart hook handler")

    # hook-end
    subparsers.add_parser("hook-end", help="Direct SessionEnd hook handler")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    custom_file = Path(args.file).resolve() if args.file else None
    registry = SessionRegistry()
    if custom_file:
        from agent_session_restore.storage import SessionStorage

        registry = SessionRegistry(storage=SessionStorage(custom_file))

    if args.subcommand == "hook-start":
        res = handle_session_start(registry=registry)
        print(json.dumps(res, ensure_ascii=False))
        return 0

    if args.subcommand == "hook-end":
        res = handle_session_end(registry=registry)
        print(json.dumps(res, ensure_ascii=False))
        return 0

    handlers = {
        "list": cmd_list,
        "restore": cmd_restore,
        "register": cmd_register,
        "unregister": cmd_unregister,
        "prune": cmd_prune,
        "export": cmd_export,
        "install-hooks": cmd_install_hooks,
    }

    handler = handlers.get(args.subcommand)
    if handler:
        return handler(args, registry)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())

