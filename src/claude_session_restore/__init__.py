"""claude-session-restore: Windows-first fast resume & session registry manager for Claude Code fleets."""

from claude_session_restore.models import SessionEntry, SessionRegistryData, SessionStatus
from claude_session_restore.registry import SessionRegistry
from claude_session_restore.storage import SessionStorage

__version__ = "0.1.0"
__all__ = [
    "SessionEntry",
    "SessionRegistryData",
    "SessionStatus",
    "SessionStorage",
    "SessionRegistry",
]
