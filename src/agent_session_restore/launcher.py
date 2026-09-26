"""Windows-first fast session resume launcher."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agent_session_restore.models import SessionEntry
from agent_session_restore.quoting import (
    format_argv_cmd,
    format_argv_powershell,
    quote_cmd_token,
    quote_powershell,
)

TerminalBackend = Literal["wt", "pwsh", "powershell", "cmd", "print"]


def _substitute_custom_cmd(template: str, session: SessionEntry, quote: Callable[[str], str]) -> str:
    """Substitute id/session_id/cwd/name placeholders in a custom resume
    command template, quoting each substituted value for the target shell
    dialect. The template text itself (authored by the user) is left
    untouched -- custom_resume_cmd is intentionally an arbitrary, trusted
    command, not something this tool can safely rewrite.
    """
    sid = session.session_id
    return (
        template.replace("{id}", quote(sid))
        .replace("{session_id}", quote(sid))
        .replace("{cwd}", quote(session.cwd))
        .replace("{name}", quote(session.name))
    )


@dataclass
class LaunchResult:
    """Outcome of a session launch command."""

    session: SessionEntry
    success: bool
    command: list[str]
    error_message: str | None = None


class SessionLauncher:
    """Executes or generates launch commands to resume Claude Code sessions."""

    def __init__(self, backend: TerminalBackend = "wt", delay_seconds: float = 0.3) -> None:
        self.backend = backend
        self.delay_seconds = delay_seconds

    @staticmethod
    def is_windows_terminal_available() -> bool:
        """Check if Windows Terminal (wt.exe) is present in PATH or standard location."""
        if shutil.which("wt.exe") or shutil.which("wt"):
            return True
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        if local_app_data:
            wt_alias = Path(local_app_data) / "Microsoft" / "WindowsApps" / "wt.exe"
            return wt_alias.is_file()
        return False

    @staticmethod
    def build_agent_argv(session: SessionEntry) -> list[str] | None:
        """Return the built-in resume command as an argv list (program + args).

        Returns None when the session has a custom_resume_cmd: that is an
        arbitrary, trusted command template (see _substitute_custom_cmd) and
        is not decomposed into argv tokens.
        """
        if session.custom_resume_cmd:
            return None

        agent = session.agent.lower()
        sid = session.session_id
        cwd = session.cwd

        agent_argv = {
            "codex": ["codex", "resume", sid],
            "cursor": ["cursor", cwd],
            "antigravity": ["agy", "resume", sid],
            "hermes": ["hermes", "resume", sid],
            "claude": ["claude", "-r", sid],
        }
        return agent_argv.get(agent, ["claude", "-r", sid])

    @staticmethod
    def get_agent_cli_command(session: SessionEntry) -> str:
        """Return the resume command as a plain, unquoted display string.

        This is for human-readable output only (dry-run previews, csr list,
        the default print backend). The command that is actually executed is
        built separately in build_resume_command using build_agent_argv plus
        per-shell quoting, so this display formatting never affects what
        actually gets run.
        """
        if session.custom_resume_cmd:
            return _substitute_custom_cmd(session.custom_resume_cmd, session, str)
        argv = SessionLauncher.build_agent_argv(session) or []
        return " ".join(argv)

    def build_resume_command(self, session: SessionEntry) -> list[str]:
        """Construct the launch command list for a specific terminal backend.

        Dynamic values (session id, cwd, name) are only ever embedded in a
        command STRING (the text handed to pwsh -Command or cmd.exe /k)
        after being quoted with the matching per-shell helper from
        agent_session_restore.quoting. Everywhere else they are passed as
        discrete argv elements, which the OS process-creation API keeps
        separate from shell re-parsing.
        """
        cwd = session.cwd
        agent_tag = session.agent.upper()
        display_title = f"{agent_tag}: {session.name}"
        argv = self.build_agent_argv(session)

        # Prefer pwsh if installed, fallback to powershell
        pwsh_cmd = "pwsh" if shutil.which("pwsh") else "powershell"

        if self.backend == "wt":
            if argv is not None:
                agent_cmd_ps = format_argv_powershell(argv)
            else:
                agent_cmd_ps = _substitute_custom_cmd(
                    session.custom_resume_cmd or "", session, quote_powershell
                )
            return [
                "wt.exe",
                "-w",
                "0",
                "new-tab",
                "-d",
                cwd,
                "--title",
                display_title,
                pwsh_cmd,
                "-NoExit",
                "-Command",
                agent_cmd_ps,
            ]

        if self.backend in ("pwsh", "powershell"):
            if argv is not None:
                agent_cmd_ps = format_argv_powershell(argv)
            else:
                agent_cmd_ps = _substitute_custom_cmd(
                    session.custom_resume_cmd or "", session, quote_powershell
                )
            shell_bin = "pwsh.exe" if self.backend == "pwsh" and shutil.which("pwsh") else "powershell.exe"
            script = (
                f"Set-Location -LiteralPath {quote_powershell(cwd)}; "
                f"$host.UI.RawUI.WindowTitle = {quote_powershell(display_title)}; "
                f"{agent_cmd_ps}"
            )
            return [shell_bin, "-NoExit", "-Command", script]

        if self.backend == "cmd":
            if argv is not None:
                agent_cmd_cmd = format_argv_cmd(argv)
            else:
                agent_cmd_cmd = _substitute_custom_cmd(
                    session.custom_resume_cmd or "", session, quote_cmd_token
                )
            # cmd.exe re-parses its *entire* command line after /c (including
            # the title and /D directory arguments), independent of how the
            # argv list was quoted for CreateProcess, so title and cwd are
            # also escaped with the cmd-specific helper here, not left raw.
            return [
                "cmd.exe",
                "/c",
                "start",
                quote_cmd_token(display_title),
                "/D",
                quote_cmd_token(cwd),
                "cmd",
                "/k",
                agent_cmd_cmd,
            ]

        return self.get_agent_cli_command(session).split()

    def launch_session(self, session: SessionEntry) -> LaunchResult:
        """Launch a single session."""
        if not session.cwd_exists:
            return LaunchResult(
                session=session,
                success=False,
                command=[],
                error_message=f"Directory does not exist: {session.cwd}",
            )

        cmd = self.build_resume_command(session)

        try:
            if self.backend == "print":
                return LaunchResult(session=session, success=True, command=cmd)

            flags = 0
            if os.name == "nt":
                flags = int(getattr(subprocess, "DETACHED_PROCESS", 0x00000008))

            # shell=False always: cmd is a fully-formed argv list, and any
            # dynamic value embedded in a command STRING within it (for the
            # pwsh/cmd backends) has already been quoted for that shell by
            # build_resume_command. Setting shell=True here would additionally
            # re-parse the whole argv list through cmd.exe/COMSPEC, which is
            # exactly the double-parsing hazard this design avoids.
            subprocess.Popen(
                cmd,
                cwd=session.cwd,
                creationflags=flags,
            )
            return LaunchResult(session=session, success=True, command=cmd)
        except Exception as e:
            return LaunchResult(session=session, success=False, command=cmd, error_message=str(e))

    def launch_many(
        self,
        sessions: list[SessionEntry],
        limit: int | None = None,
    ) -> list[LaunchResult]:
        """Launch multiple sessions sequentially with slight spacing to avoid disk/CPU spikes."""
        to_launch = sessions[:limit] if limit else sessions
        results: list[LaunchResult] = []

        for i, session in enumerate(to_launch):
            res = self.launch_session(session)
            results.append(res)
            if self.delay_seconds > 0 and i < len(to_launch) - 1:
                time.sleep(self.delay_seconds)

        return results

    def generate_powershell_script(self, sessions: list[SessionEntry]) -> str:
        """Generate a complete PowerShell restore script that can be saved or scheduled."""
        lines = [
            "# Windows 11 Multi-Agent Session Restore Script",
            "# Generated by agent-session-restore (Agent Fleet Restorer)",
            "$ErrorActionPreference = 'SilentlyContinue'",
            "",
            f"Write-Host 'Restoring {len(sessions)} Agent sessions...' -ForegroundColor Cyan",
            "",
        ]

        use_wt = self.is_windows_terminal_available()
        pwsh_cmd = "pwsh" if shutil.which("pwsh") else "powershell"

        for s in sessions:
            argv = self.build_agent_argv(s)
            if argv is not None:
                agent_cmd_ps = format_argv_powershell(argv)
            else:
                agent_cmd_ps = _substitute_custom_cmd(s.custom_resume_cmd or "", s, quote_powershell)

            agent_tag = s.agent.upper()
            display_title = f"{agent_tag}: {s.name}"
            cwd_q = quote_powershell(s.cwd)
            title_q = quote_powershell(display_title)

            lines.append(f"# [{agent_tag}] Session: {s.name} ({s.session_id})")
            lines.append(f"if (Test-Path -LiteralPath {cwd_q}) {{")

            if use_wt:
                lines.append(
                    f"    wt.exe -w 0 new-tab -d {cwd_q} --title {title_q} "
                    f"{pwsh_cmd} -NoExit -Command {quote_powershell(agent_cmd_ps)}"
                )
            else:
                inner_script = f"$host.UI.RawUI.WindowTitle = {title_q}; {agent_cmd_ps}"
                lines.append(
                    f"    Start-Process {pwsh_cmd} -WorkingDirectory {cwd_q} "
                    f"-ArgumentList '-NoExit', '-Command', {quote_powershell(inner_script)}"
                )

            lines.append("    Start-Sleep -Milliseconds 300")
            lines.append("} else {")
            warn_text = f"Skipping [{agent_tag}] {s.name}: Directory {s.cwd} not found"
            lines.append(f"    Write-Warning {quote_powershell(warn_text)}")
            lines.append("}")
            lines.append("")

        lines.append("Write-Host 'All sessions launched!' -ForegroundColor Green")
        return "\n".join(lines)
