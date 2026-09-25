"""Windows-first fast session resume launcher."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agent_session_restore.models import SessionEntry

TerminalBackend = Literal["wt", "pwsh", "powershell", "cmd", "print"]


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
    def get_agent_cli_command(session: SessionEntry) -> str:
        """Derive the specific resume CLI command based on agent type."""
        agent = session.agent.lower()
        sid = session.session_id
        cwd = session.cwd

        if session.custom_resume_cmd:
            cmd = session.custom_resume_cmd
            cmd = cmd.replace("{id}", sid).replace("{session_id}", sid)
            cmd = cmd.replace("{cwd}", cwd).replace("{name}", session.name)
            return cmd

        agent_commands = {
            "codex": f"codex resume {sid}",
            "cursor": f"cursor '{cwd}'",
            "antigravity": f"agy resume {sid}",
            "hermes": f"hermes resume {sid}",
            "claude": f"claude -r {sid}",
        }
        return agent_commands.get(agent, f"claude -r {sid}")

    def build_resume_command(self, session: SessionEntry) -> list[str]:
        """Construct the launch command list for a specific terminal backend."""
        cwd = session.cwd
        agent_tag = session.agent.upper()
        display_title = f"{agent_tag}: {session.name}"
        agent_cmd = self.get_agent_cli_command(session)

        # Prefer pwsh if installed, fallback to powershell
        pwsh_cmd = "pwsh" if shutil.which("pwsh") else "powershell"

        if self.backend == "wt":
            # Windows Terminal new tab with custom title and directory
            return [
                "wt.exe",
                "-w",
                "0",  # Reuse current or first window
                "new-tab",
                "-d",
                cwd,
                "--title",
                display_title,
                pwsh_cmd,
                "-NoExit",
                "-Command",
                agent_cmd,
            ]

        if self.backend in ("pwsh", "powershell"):
            # Standalone PowerShell window
            shell_bin = "pwsh.exe" if self.backend == "pwsh" and shutil.which("pwsh") else "powershell.exe"
            return [
                shell_bin,
                "-NoExit",
                "-Command",
                f"Set-Location -LiteralPath '{cwd}'; $host.UI.RawUI.WindowTitle = '{display_title}'; {agent_cmd}",
            ]

        if self.backend == "cmd":
            # Standalone CMD window
            return [
                "cmd.exe",
                "/c",
                "start",
                display_title,
                "/D",
                cwd,
                "cmd",
                "/k",
                agent_cmd,
            ]

        # Default / print backend
        return agent_cmd.split()

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
                # DETACHED_PROCESS = 0x00000008, so spawned window doesn't hold parent console
                flags = int(getattr(subprocess, "DETACHED_PROCESS", 0x00000008))

            subprocess.Popen(
                cmd,
                cwd=session.cwd,
                creationflags=flags,
                shell=(self.backend == "cmd"),
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

        # If wt backend and multiple sessions, we can also launch them smoothly
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
            "# Generated by claude-session-restore (Agent Fleet Restorer)",
            "$ErrorActionPreference = 'SilentlyContinue'",
            "",
            f"Write-Host 'Restoring {len(sessions)} Agent sessions...' -ForegroundColor Cyan",
            "",
        ]

        use_wt = self.is_windows_terminal_available()
        pwsh_cmd = "pwsh" if shutil.which("pwsh") else "powershell"

        for s in sessions:
            escaped_cwd = s.cwd.replace("'", "''")
            escaped_name = s.name.replace("'", "''")
            agent_tag = s.agent.upper()
            display_title = f"{agent_tag}: {escaped_name}"
            agent_cmd = self.get_agent_cli_command(s).replace("'", "''")

            lines.append(f"# [{agent_tag}] Session: {s.name} ({s.session_id})")
            lines.append(f"if (Test-Path -LiteralPath '{escaped_cwd}') {{")

            if use_wt:
                lines.append(
                    f"    wt.exe -w 0 new-tab -d '{escaped_cwd}' --title '{display_title}' {pwsh_cmd} -NoExit -Command '{agent_cmd}'"
                )
            else:
                lines.append(
                    f"    Start-Process {pwsh_cmd} -WorkingDirectory '{escaped_cwd}' -ArgumentList '-NoExit', '-Command', '$host.UI.RawUI.WindowTitle = ''{display_title}''; {agent_cmd}'"
                )

            lines.append("    Start-Sleep -Milliseconds 300")
            lines.append("} else {")
            lines.append(f"    Write-Warning 'Skipping [{agent_tag}] {escaped_name}: Directory {escaped_cwd} not found'")
            lines.append("}")
            lines.append("")

        lines.append("Write-Host 'All sessions launched!' -ForegroundColor Green")
        return "\n".join(lines)

