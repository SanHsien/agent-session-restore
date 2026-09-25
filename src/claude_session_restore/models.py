"""Data models for Claude Code session entries and registry."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


class SessionStatus(str, Enum):
    """Lifecycle status of a recorded Claude session."""

    ACTIVE = "active"
    CLOSED = "closed"
    ARCHIVED = "archived"


def _current_iso_timestamp() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SessionEntry:
    """Represents a single registered Claude Code session."""

    session_id: str
    name: str
    cwd: str
    status: str = SessionStatus.ACTIVE.value
    created_at: str = field(default_factory=_current_iso_timestamp)
    updated_at: str = field(default_factory=_current_iso_timestamp)
    git_branch: str | None = None
    pid: int | None = None
    notes: str | None = None

    def __post_init__(self) -> None:
        """Normalize paths and ensure required fields are valid."""
        if not self.session_id or not self.session_id.strip():
            raise ValueError("session_id must not be empty")

        # Normalize working directory path
        try:
            self.cwd = str(Path(self.cwd).resolve())
        except Exception:
            self.cwd = os.path.normpath(self.cwd)

        # Fallback name if empty
        if not self.name or not self.name.strip():
            dir_name = Path(self.cwd).name or "workspace"
            self.name = f"{dir_name}-{self.session_id[:8]}"
        else:
            self.name = self.name.strip()

    @property
    def is_active(self) -> bool:
        """Check if session is currently marked active."""
        return self.status == SessionStatus.ACTIVE.value

    @property
    def cwd_exists(self) -> bool:
        """Check if the session workspace directory exists on disk."""
        return Path(self.cwd).is_dir()

    def touch(self, status: str | None = None) -> None:
        """Update last active timestamp and optionally change status."""
        self.updated_at = _current_iso_timestamp()
        if status:
            self.status = status

    def to_dict(self) -> dict[str, Any]:
        """Convert entry to dictionary for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionEntry:
        """Construct a SessionEntry from dictionary safely."""
        return cls(
            session_id=str(data.get("session_id", "")),
            name=str(data.get("name", "")),
            cwd=str(data.get("cwd", "")),
            status=str(data.get("status", SessionStatus.ACTIVE.value)),
            created_at=str(data.get("created_at", _current_iso_timestamp())),
            updated_at=str(data.get("updated_at", _current_iso_timestamp())),
            git_branch=data.get("git_branch"),
            pid=data.get("pid"),
            notes=data.get("notes"),
        )


@dataclass
class SessionRegistryData:
    """Root structure of the persisted sessions file."""

    version: int = 1
    updated_at: str = field(default_factory=_current_iso_timestamp)
    sessions: dict[str, SessionEntry] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert entire registry to dictionary for storage."""
        return {
            "version": self.version,
            "updated_at": self.updated_at,
            "sessions": {sid: entry.to_dict() for sid, entry in self.sessions.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionRegistryData:
        """Construct RegistryData from dictionary safely."""
        version = int(data.get("version", 1))
        updated_at = str(data.get("updated_at", _current_iso_timestamp()))
        sessions_raw = data.get("sessions", {})
        sessions: dict[str, SessionEntry] = {}

        if isinstance(sessions_raw, dict):
            for sid, sdata in sessions_raw.items():
                if isinstance(sdata, dict):
                    try:
                        sessions[sid] = SessionEntry.from_dict(sdata)
                    except Exception:
                        continue
        elif isinstance(sessions_raw, list):
            # Backward-compatibility if stored as a list
            for sdata in sessions_raw:
                if isinstance(sdata, dict) and "session_id" in sdata:
                    try:
                        entry = SessionEntry.from_dict(sdata)
                        sessions[entry.session_id] = entry
                    except Exception:
                        continue

        return cls(version=version, updated_at=updated_at, sessions=sessions)
