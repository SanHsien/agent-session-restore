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
    from agent_session_restore.hooks import handle_session_end

    result = handle_session_end()
    # ASCII-only JSON: stdout uses the console code page, which could garble
    # or reject non-ASCII text; JSON escapes are lossless and always encodable.
    print(json.dumps(result))
except Exception as e:
    try:
        log_path = Path.home() / ".claude" / "session-restore-hook.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"SessionEnd error: {e}\n")
    except Exception:
        pass  # logging must never turn a handled error into a failing hook
    print(json.dumps({"systemMessage": ""}))

sys.exit(0)
