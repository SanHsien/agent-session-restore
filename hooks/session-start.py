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
    from claude_session_restore.hooks import handle_session_start

    result = handle_session_start()
    print(json.dumps(result, ensure_ascii=False))
except Exception as e:
    # Silent fail-safe: never block Claude from starting
    with open(Path.home() / ".claude" / "session-restore-hook.log", "a", encoding="utf-8") as f:
        f.write(f"SessionStart error: {e}\n")
    print(json.dumps({"systemMessage": ""}))

sys.exit(0)
