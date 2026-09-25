"""agent-session-restore: Windows-first fast resume & session registry manager for AI Agent fleets."""

from agent_session_restore.models import AgentType, SessionEntry, SessionRegistryData, SessionStatus
from agent_session_restore.registry import SessionRegistry
from agent_session_restore.storage import SessionStorage

__version__ = "0.2.0"
__all__ = [
    "AgentType",
    "SessionEntry",
    "SessionRegistryData",
    "SessionStatus",
    "SessionStorage",
    "SessionRegistry",
]

