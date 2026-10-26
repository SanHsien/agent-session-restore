"""hooks/session-start.py and hooks/session-end.py, run the way Claude Code runs them.

Each script is started as a plain python child process (captured pipes, no
console window) with a throwaway HOME and registry file, so nothing outside
the temp directory is read or written and no terminal is launched.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parent.parent / "hooks"


def _run_hook(
    script: str, payload: dict[str, str], home: Path, registry_file: Path
) -> subprocess.CompletedProcess[bytes]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_")}
    env.update(
        HOME=str(home),
        USERPROFILE=str(home),
        CLAUDE_SESSION_REGISTRY_FILE=str(registry_file),
    )
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    return subprocess.run(
        [sys.executable, str(HOOKS_DIR / script)],
        input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        capture_output=True,
        env=env,
        timeout=60,
        creationflags=flags,
    )


def test_session_start_hook_stdout_is_ascii_json_and_records_non_ascii_names(tmp_path):
    registry_file = tmp_path / "sessions.json"
    proc = _run_hook(
        "session-start.py",
        {"session_id": "hook-1", "session_name": "專案修復", "cwd": str(tmp_path)},
        tmp_path / "home",
        registry_file,
    )

    assert proc.returncode == 0, proc.stderr
    out = proc.stdout.decode("ascii")  # raises if any non-ASCII byte was written
    assert "專案修復" in json.loads(out)["systemMessage"]
    stored = json.loads(registry_file.read_text("utf-8"))
    assert stored["sessions"]["hook-1"]["name"] == "專案修復"


def test_session_end_hook_marks_the_session_closed(tmp_path):
    registry_file = tmp_path / "sessions.json"
    home = tmp_path / "home"
    _run_hook("session-start.py", {"session_id": "hook-2", "cwd": str(tmp_path)}, home, registry_file)

    proc = _run_hook("session-end.py", {"session_id": "hook-2"}, home, registry_file)

    assert proc.returncode == 0, proc.stderr
    assert "hook-2" in json.loads(proc.stdout.decode("ascii"))["systemMessage"]
    stored = json.loads(registry_file.read_text("utf-8"))
    assert stored["sessions"]["hook-2"]["status"] == "closed"


# (script, payload, what makes the handler fail)
FAILING = [
    ("session-start.py", {"session_id": "bad id", "cwd": "."}, "invalid session id"),
    ("session-end.py", {"session_id": "any-id"}, "registry path is a directory"),
]


def _make_it_fail(case: str, tmp_path: Path) -> Path:
    if case == "registry path is a directory":
        registry_file = tmp_path / "registry-is-a-dir"
        registry_file.mkdir()
        return registry_file
    return tmp_path / "sessions.json"


@pytest.mark.parametrize(("script", "payload", "case"), FAILING)
def test_hook_error_is_logged_and_the_hook_still_exits_zero(script, payload, case, tmp_path):
    home = tmp_path / "home"
    proc = _run_hook(script, payload, home, _make_it_fail(case, tmp_path))

    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout.decode("ascii")) == {"systemMessage": ""}
    log = (home / ".claude" / "session-restore-hook.log").read_text("utf-8")
    assert "error" in log


@pytest.mark.parametrize(("script", "payload", "case"), FAILING)
def test_hook_still_exits_zero_when_the_error_log_cannot_be_written(
    script, payload, case, tmp_path
):
    # ~/.claude is a regular file, so neither mkdir nor open() can create the log.
    home = tmp_path / "home"
    home.mkdir()
    (home / ".claude").write_text("not a directory", encoding="utf-8")

    proc = _run_hook(script, payload, home, _make_it_fail(case, tmp_path))

    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout.decode("ascii")) == {"systemMessage": ""}
