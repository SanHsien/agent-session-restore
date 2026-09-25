#!/usr/bin/env python3
"""Claude Code SessionEnd Hook.

Invoked automatically when a Claude Code session gracefully terminates.
Marks the session as closed in the registry.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

try:
    try:
        from agent_session_restore.hooks import handle_session_end
    except ImportError:
        from claude_session_restore.hooks import handle_session_end

    result = handle_session_end()
    print(json.dumps(result, ensure_ascii=False))
except Exception as e:
    with open(Path.home() / ".claude" / "session-restore-hook.log", "a", encoding="utf-8") as f:
        f.write(f"SessionEnd error: {e}\n")
    print(json.dumps({"systemMessage": ""}))

sys.exit(0)
