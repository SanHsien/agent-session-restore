"""The hooks configuration this project hands to users must be valid for Claude Code.

Claude Code's settings.json expects every event to hold a list of matcher
groups, each with a nested `hooks` list of handlers:

    {"matcher": "...", "hooks": [{"type": "command", "command": "..."}]}

The flat shape `{"matcher": "...", "command": "..."}` is rejected by Claude
Code's settings validation, so hooks configured that way never ran.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from agent_session_restore.cli import main

REPO_ROOT = Path(__file__).resolve().parent.parent


def _assert_valid_hooks_config(config: dict[str, Any]) -> None:
    hooks = config["hooks"]
    assert set(hooks) >= {"SessionStart", "SessionEnd"}
    for event in ("SessionStart", "SessionEnd"):
        groups = hooks[event]
        assert groups, event
        for group in groups:
            assert "command" not in group, f"{event}: flat handler is invalid"
            assert isinstance(group["hooks"], list) and group["hooks"], event
            for handler in group["hooks"]:
                assert handler["type"] == "command"
                assert isinstance(handler["command"], str) and handler["command"]


def test_install_hooks_prints_a_valid_claude_code_hooks_config(capsys):
    assert main(["install-hooks"]) == 0
    out = capsys.readouterr().out
    config = json.loads(out[out.index("{") : out.rindex("}") + 1])
    _assert_valid_hooks_config(config)
    assert "session-start.py" in json.dumps(config["hooks"]["SessionStart"])
    assert "session-end.py" in json.dumps(config["hooks"]["SessionEnd"])


def test_shipped_settings_snippet_is_valid():
    snippet = json.loads((REPO_ROOT / "hooks" / "claude-settings-snippet.json").read_text("utf-8"))
    _assert_valid_hooks_config(snippet)


@pytest.mark.parametrize("readme", ["README.md", "README.en.md"])
def test_readme_hooks_example_is_valid(readme):
    text = (REPO_ROOT / readme).read_text("utf-8")
    blocks = re.findall(r"```json\n(.*?)```", text, flags=re.DOTALL)
    hook_blocks = [json.loads(b) for b in blocks if '"SessionStart"' in b]
    assert hook_blocks, f"{readme} has no hooks example"
    for block in hook_blocks:
        _assert_valid_hooks_config(block)


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 not available")
def test_install_hooks_script_writes_a_valid_config_and_replaces_old_entries(tmp_path):
    # Runs scripts/Install-Hooks.ps1 against a throwaway settings file. It only
    # writes that file; no terminal or window is started.
    settings = tmp_path / "settings.json"
    stale = {
        "hooks": {
            "SessionStart": [
                {"matcher": "startup", "command": 'python "C:/old/hooks/session-start.py"'},
                {"hooks": [{"type": "command", "command": "other-tool"}]},
            ]
        }
    }
    settings.write_text(json.dumps(stale), encoding="utf-8")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0

    proc = subprocess.run(
        ["pwsh", "-NoProfile", "-NonInteractive", "-File",
         str(REPO_ROOT / "scripts" / "Install-Hooks.ps1"), "-SettingsPath", str(settings)],
        capture_output=True, text=True, timeout=120, creationflags=flags,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr

    config = json.loads(settings.read_text("utf-8-sig"))
    _assert_valid_hooks_config(config)
    commands = [h["command"] for g in config["hooks"]["SessionStart"] for h in g["hooks"]]
    assert "other-tool" in commands
    assert sum("session-start.py" in c for c in commands) == 1
    assert "C:/old/hooks" not in " ".join(commands)


def _run_install_script(settings: Path) -> subprocess.CompletedProcess[str]:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    return subprocess.run(
        ["pwsh", "-NoProfile", "-NonInteractive", "-File",
         str(REPO_ROOT / "scripts" / "Install-Hooks.ps1"), "-SettingsPath", str(settings)],
        capture_output=True, text=True, timeout=120, creationflags=flags,
    )


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 not available")
def test_install_hooks_script_works_when_settings_json_does_not_exist(tmp_path):
    # A fresh machine has no settings.json: the script starts from an ordered
    # dictionary, which has no ContainsKey() (only Contains()), and crashed.
    settings = tmp_path / "newdir" / "settings.json"

    proc = _run_install_script(settings)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    _assert_valid_hooks_config(json.loads(settings.read_text("utf-8-sig")))


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 not available")
def test_install_hooks_script_starts_fresh_when_settings_json_is_unparsable(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text("{ this is not json", encoding="utf-8")

    proc = _run_install_script(settings)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    _assert_valid_hooks_config(json.loads(settings.read_text("utf-8-sig")))
