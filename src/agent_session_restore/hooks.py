"""Claude Code Hook handlers for SessionStart and SessionEnd events."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from agent_session_restore.models import SessionStatus
from agent_session_restore.registry import SessionRegistry


def parse_hook_payload(raw_input: str | None = None) -> dict[str, Any]:
    """Parse JSON payload from stdin or given string. Returns dict with fallback."""
    if raw_input is None:
        try:
            if not sys.stdin.isatty():
                raw_input = sys.stdin.read()
        except Exception:
            raw_input = None

    if raw_input and raw_input.strip():
        try:
            parsed = json.loads(raw_input)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

    # Fallback to environment variables
    env_payload: dict[str, Any] = {}
    if os.environ.get("CLAUDE_SESSION_ID"):
        env_payload["session_id"] = os.environ["CLAUDE_SESSION_ID"]
    if os.environ.get("CLAUDE_SESSION_NAME"):
        env_payload["session_name"] = os.environ["CLAUDE_SESSION_NAME"]
    if os.environ.get("CLAUDE_PROJECT_DIR"):
        env_payload["cwd"] = os.environ["CLAUDE_PROJECT_DIR"]

    return env_payload


def handle_session_start(
    payload: dict[str, Any] | None = None,
    registry: SessionRegistry | None = None,
) -> dict[str, Any]:
    """Process SessionStart event and record session in registry."""
    if payload is None:
        payload = parse_hook_payload()

    reg = registry or SessionRegistry()

    # Extract session identifier
    session_id = str(
        payload.get("session_id")
        or payload.get("sessionId")
        or payload.get("id")
        or os.environ.get("CLAUDE_SESSION_ID")
        or ""
    ).strip()

    # Working directory
    cwd = str(
        payload.get("cwd")
        or payload.get("project_dir")
        or payload.get("workspace")
        or os.getcwd()
    ).strip()

    # Session Name (-n or /rename)
    name = str(
        payload.get("session_name")
        or payload.get("name")
        or payload.get("sessionName")
        or os.environ.get("CLAUDE_SESSION_NAME")
        or ""
    ).strip()

    if not name:
        # Generate intuitive name based on folder name
        dir_name = Path(cwd).name or "workspace"
        name = dir_name

    if not session_id:
        # If no session ID found, we cannot track effectively
        return {
            "systemMessage": f"[claude-session-restore] Warning: No session_id found for '{name}'."
        }

    agent = str(
        payload.get("agent")
        or payload.get("agent_type")
        or os.environ.get("AGENT_TYPE")
        or "claude"
    ).strip().lower()

    custom_resume_cmd = payload.get("custom_resume_cmd") or os.environ.get("CUSTOM_RESUME_CMD")

    pid = os.getppid() if hasattr(os, "getppid") else None

    entry = reg.register(
        session_id=session_id,
        name=name,
        cwd=cwd,
        agent=agent,
        status=SessionStatus.ACTIVE.value,
        pid=pid,
        custom_resume_cmd=custom_resume_cmd,
    )

    return {
        "systemMessage": f"Session tracked by claude-session-restore: [{entry.agent.upper()}] '{entry.name}' [{session_id[:8]}]"
    }


def handle_session_end(
    payload: dict[str, Any] | None = None,
    registry: SessionRegistry | None = None,
) -> dict[str, Any]:
    """Process SessionEnd event and mark session as closed."""
    if payload is None:
        payload = parse_hook_payload()

    reg = registry or SessionRegistry()

    session_id = str(
        payload.get("session_id")
        or payload.get("sessionId")
        or payload.get("id")
        or os.environ.get("CLAUDE_SESSION_ID")
        or ""
    ).strip()

    if session_id:
        reg.unregister(session_id, hard_delete=False)
        return {
            "systemMessage": f"Session closed in claude-session-restore: [{session_id[:8]}]"
        }

    return {"systemMessage": "[claude-session-restore] SessionEnd processed."}

