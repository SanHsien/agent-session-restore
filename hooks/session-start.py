#!/usr/bin/env python3
"""Claude Code SessionStart Hook.

Invoked automatically when a Claude Code session begins or resumes.
Reads session info from stdin/env and updates the session registry.
Guaranteed to exit 0 and never interrupt Claude Code startup.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Add src to sys.path so it works seamlessly without global install
_SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

try:
    from agent_session_restore.hooks import handle_session_start

    result = handle_session_start()
    # ASCII-only JSON: stdout uses the console code page, which could garble
    # or reject non-ASCII text; JSON escapes are lossless and always encodable.
    print(json.dumps(result))
except Exception as e:
    # Silent fail-safe: never block Claude from starting
    try:
        log_path = Path.home() / ".claude" / "session-restore-hook.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"SessionStart error: {e}\n")
    except Exception:
        pass  # logging must never turn a handled error into a failing hook
    print(json.dumps({"systemMessage": ""}))

sys.exit(0)
