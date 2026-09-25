"""Session registry business logic and manager."""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

from claude_session_restore.models import (
    SessionEntry,
    SessionStatus,
    _current_iso_timestamp,
)
from claude_session_restore.storage import SessionStorage


def detect_git_branch(cwd: str | Path) -> str | None:
    """Attempt to detect current git branch in the target directory."""
    try:
        flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
        result = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            creationflags=flags,
            check=False,
        )
        if result.returncode == 0:
            branch = result.stdout.strip()
            return branch if branch and branch != "HEAD" else None
    except Exception:
        pass
    return None


class SessionRegistry:
    """High-level manager for reading and modifying the session registry."""

    def __init__(self, storage: SessionStorage | None = None) -> None:
        self.storage = storage or SessionStorage()

    def register(
        self,
        session_id: str,
        name: str | None = None,
        cwd: str | None = None,
        status: str = SessionStatus.ACTIVE.value,
        git_branch: str | None = None,
        pid: int | None = None,
        notes: str | None = None,
    ) -> SessionEntry:
        """Register a new session or update an existing session."""
        reg_data = self.storage.load()
        now = _current_iso_timestamp()

        target_cwd = cwd or str(Path.cwd())
        inferred_branch = git_branch or detect_git_branch(target_cwd)

        if session_id in reg_data.sessions:
            entry = reg_data.sessions[session_id]
            if name and name.strip():
                entry.name = name.strip()
            if cwd and cwd.strip():
                entry.cwd = str(Path(cwd).resolve())
            entry.status = status
            entry.updated_at = now
            if inferred_branch:
                entry.git_branch = inferred_branch
            if pid is not None:
                entry.pid = pid
            if notes is not None:
                entry.notes = notes
        else:
            entry = SessionEntry(
                session_id=session_id,
                name=name or "",
                cwd=target_cwd,
                status=status,
                created_at=now,
                updated_at=now,
                git_branch=inferred_branch,
                pid=pid,
                notes=notes,
            )
            reg_data.sessions[session_id] = entry

        reg_data.updated_at = now
        self.storage.save(reg_data)
        return entry

    def unregister(self, session_id: str, hard_delete: bool = False) -> bool:
        """Mark a session as closed, or completely delete it if hard_delete is True."""
        reg_data = self.storage.load()
        if session_id not in reg_data.sessions:
            return False

        if hard_delete:
            del reg_data.sessions[session_id]
        else:
            entry = reg_data.sessions[session_id]
            entry.status = SessionStatus.CLOSED.value
            entry.updated_at = _current_iso_timestamp()

        reg_data.updated_at = _current_iso_timestamp()
        self.storage.save(reg_data)
        return True

    def get(self, session_id: str) -> SessionEntry | None:
        """Retrieve a specific session by ID."""
        reg_data = self.storage.load()
        return reg_data.sessions.get(session_id)

    def list_sessions(
        self,
        active_only: bool = False,
        cwd_filter: str | None = None,
    ) -> list[SessionEntry]:
        """List sessions ordered by most recently updated first."""
        reg_data = self.storage.load()
        entries = list(reg_data.sessions.values())

        if active_only:
            entries = [e for e in entries if e.is_active]

        if cwd_filter:
            norm_filter = str(Path(cwd_filter).resolve()).lower()
            entries = [
                e
                for e in entries
                if norm_filter in str(Path(e.cwd).resolve()).lower()
            ]

        # Sort by updated_at descending
        entries.sort(key=lambda e: e.updated_at, reverse=True)
        return entries

    def prune(
        self,
        max_age_days: int = 14,
        keep_active: bool = True,
        remove_missing_cwd: bool = False,
    ) -> int:
        """Prune stale sessions. Returns count of pruned sessions."""
        reg_data = self.storage.load()
        now = datetime.now(timezone.utc)
        to_delete: list[str] = []

        for sid, entry in reg_data.sessions.items():
            if keep_active and entry.is_active:
                continue

            if remove_missing_cwd and not entry.cwd_exists:
                to_delete.append(sid)
                continue

            try:
                updated_dt = datetime.fromisoformat(entry.updated_at)
                age_days = (now - updated_dt).total_seconds() / 86400
                if age_days > max_age_days:
                    to_delete.append(sid)
            except Exception:
                # If timestamp unparseable and not active, prune it
                if not entry.is_active:
                    to_delete.append(sid)

        for sid in to_delete:
            reg_data.sessions.pop(sid, None)

        if to_delete:
            reg_data.updated_at = _current_iso_timestamp()
            self.storage.save(reg_data)

        return len(to_delete)

    def export_markdown(self, active_only: bool = False) -> str:
        """Render markdown table summary of tracked sessions."""
        sessions = self.list_sessions(active_only=active_only)
        lines = [
            f"# Claude Code Sessions ({len(sessions)} tracked)",
            "",
            "| Session Name | ID | Status | Git Branch | Working Directory | Last Active |",
            "| :--- | :--- | :--- | :--- | :--- | :--- |",
        ]
        for s in sessions:
            status_icon = "🟢" if s.is_active else "⚪"
            branch = s.git_branch or "-"
            short_id = s.session_id[:12]
            lines.append(
                f"| `{s.name}` | `{short_id}` | {status_icon} {s.status} | `{branch}` | `{s.cwd}` | {s.updated_at} |"
            )
        lines.append("")
        return "\n".join(lines)
