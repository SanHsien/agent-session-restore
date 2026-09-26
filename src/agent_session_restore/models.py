"""Data models for Claude Code session entries and registry."""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

# Session ids are internal identifiers (UUIDs, hook-provided ids, or CLI
# slugs). They are interpolated into shell command strings for the built-in
# per-agent resume templates (see launcher.py), so they are restricted to a
# safe, strict allowlist rather than escaped -- there is no legitimate reason
# for a session id to contain whitespace or shell metacharacters.
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")

# Characters that are always illegal in a Windows path, plus ASCII control
# characters (0x00-0x1F) and DEL (0x7F). Rejecting these keeps cwd values
# restricted to syntactically valid filesystem paths -- a real absolute path
# cannot smuggle shell metacharacters like `;`, backticks, or `$(...)` that
# happen to also be forbidden path characters, and it makes obviously bogus
# values (e.g. containing a literal `<`, `|`, or `"`) fail fast at
# registration time instead of silently reaching a launch command. This does
# NOT require the directory to exist: prune --missing-dirs and the launcher's
# own "directory does not exist" handling both depend on being able to load
# and inspect entries whose cwd has since been deleted.
_FORBIDDEN_CWD_CHARS = frozenset('<>"|?*') | {chr(c) for c in range(0x20)} | {chr(0x7F)}


def _validate_session_id(session_id: str) -> None:
    if not SESSION_ID_PATTERN.match(session_id):
        raise ValueError(
            "session_id must match "
            f"{SESSION_ID_PATTERN.pattern!r} (letters, digits, '.', '_', ':', '-' only)"
        )


def _validate_cwd_chars(cwd: str) -> None:
    bad = _FORBIDDEN_CWD_CHARS.intersection(cwd)
    if bad:
        raise ValueError(f"cwd contains forbidden character(s): {sorted(bad)!r}")


def _strip_control_chars(value: str) -> str:
    """Remove ASCII control characters (0x00-0x1F) and DEL (0x7F)."""
    return "".join(ch for ch in value if ord(ch) >= 0x20 and ord(ch) != 0x7F)


class SessionStatus(str, Enum):
    """Lifecycle status of a recorded Claude session."""

    ACTIVE = "active"
    CLOSED = "closed"
    ARCHIVED = "archived"


class AgentType(str, Enum):
    """Supported agent types."""

    CLAUDE = "claude"
    CODEX = "codex"
    CURSOR = "cursor"
    ANTIGRAVITY = "antigravity"
    HERMES = "hermes"
    CUSTOM = "custom"


def _current_iso_timestamp() -> str:
    """Generate ISO 8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SessionEntry:
    """Represents a single registered agent session."""

    session_id: str
    name: str
    cwd: str
    agent: str = AgentType.CLAUDE.value
    status: str = SessionStatus.ACTIVE.value
    created_at: str = field(default_factory=_current_iso_timestamp)
    updated_at: str = field(default_factory=_current_iso_timestamp)
    git_branch: str | None = None
    pid: int | None = None
    notes: str | None = None
    custom_resume_cmd: str | None = None

    def __post_init__(self) -> None:
        """Normalize paths and ensure required fields are valid.

        session_id and cwd are validated strictly (reject rather than
        escape): both are interpolated into shell command strings for the
        built-in per-agent resume templates in launcher.py, and there is no
        legitimate reason for either to contain shell metacharacters. name
        is free-form display text, so it is only stripped of control
        characters rather than rejected -- launcher.py is responsible for
        quoting it safely wherever it is embedded in a command string.
        """
        if not self.session_id or not self.session_id.strip():
            raise ValueError("session_id must not be empty")
        _validate_session_id(self.session_id)

        # Normalize agent type
        if self.agent:
            self.agent = self.agent.strip().lower()
        else:
            self.agent = AgentType.CLAUDE.value

        # Normalize working directory path
        try:
            self.cwd = str(Path(self.cwd).resolve())
        except Exception:
            self.cwd = os.path.normpath(self.cwd)
        _validate_cwd_chars(self.cwd)

        # Fallback name if empty
        if not self.name or not self.name.strip():
            dir_name = Path(self.cwd).name or "workspace"
            self.name = f"{dir_name}-{self.session_id[:8]}"
        else:
            self.name = _strip_control_chars(self.name.strip())

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
            agent=str(data.get("agent", AgentType.CLAUDE.value)),
            status=str(data.get("status", SessionStatus.ACTIVE.value)),
            created_at=str(data.get("created_at", _current_iso_timestamp())),
            updated_at=str(data.get("updated_at", _current_iso_timestamp())),
            git_branch=data.get("git_branch"),
            pid=data.get("pid"),
            notes=data.get("notes"),
            custom_resume_cmd=data.get("custom_resume_cmd"),
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

