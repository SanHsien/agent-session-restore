"""scripts/Restore-AgentSessions.ps1 must hand real, correctly quoted arguments to Start-Process.

Start-Process joins an -ArgumentList array with plain spaces and does not
quote the elements, so a title or directory containing a space was split into
several arguments (Windows Terminal then ran the words of the session name as
a command). Start-Process is shadowed by a recording function in these tests,
so no terminal, window or process is ever started.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "Restore-AgentSessions.ps1"

pytestmark = pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 not available")

RECORDER = r"""
$global:calls = @()
function Start-Process {
    [CmdletBinding()]
    param([Parameter(Position = 0)]$FilePath, [object]$ArgumentList, $WorkingDirectory)
    # Same semantics as the real cmdlet: an array is joined with single spaces.
    $text = if ($ArgumentList -is [array]) { $ArgumentList -join ' ' } else { [string]$ArgumentList }
    $global:calls += [pscustomobject]@{ FilePath = [string]$FilePath; ArgText = $text }
}
& '{script}' -Terminal {terminal} -RegistryFile '{registry}' -DelayMs 0 *> $null
ConvertTo-Json -InputObject @($global:calls) -Compress | Set-Content -LiteralPath '{out}' -Encoding utf8
"""


def _run(tmp_path: Path, terminal: str, name: str, project: Path) -> list[dict[str, str]]:
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "version": 1,
                "sessions": {
                    "s-1": {
                        "session_id": "s-1",
                        "name": name,
                        "cwd": str(project),
                        "agent": "claude",
                        "status": "active",
                        "updated_at": "2026-01-01T00:00:00+00:00",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "calls.json"
    code = (
        RECORDER.replace("{script}", str(SCRIPT))
        .replace("{terminal}", terminal)
        .replace("{registry}", str(registry))
        .replace("{out}", str(out))
    )
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    proc = subprocess.run(
        ["pwsh", "-NoProfile", "-NonInteractive", "-Command", code],
        capture_output=True, text=True, timeout=120, creationflags=flags,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    calls = json.loads(out.read_text("utf-8-sig"))
    assert isinstance(calls, list) and len(calls) == 1
    return calls


def test_wt_title_and_directory_with_spaces_stay_single_arguments(tmp_path):
    project = tmp_path / "my project"
    project.mkdir()

    (call,) = _run(tmp_path, "wt", "my feature work", project)

    assert call["FilePath"] == "wt.exe"
    assert f'-d "{project}"' in call["ArgText"]
    assert '--title "[CLAUDE] my feature work"' in call["ArgText"]


def test_pwsh_window_title_with_spaces_is_a_single_command_argument(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()

    (call,) = _run(tmp_path, "pwsh", "my feature work", project)

    # The whole -Command text is one quoted argument, so PowerShell receives it intact.
    assert "-Command \"$host.UI.RawUI.WindowTitle = '[CLAUDE] my feature work'; " in call["ArgText"]


def test_wt_title_with_embedded_quotes_is_escaped(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()

    (call,) = _run(tmp_path, "wt", 'say "hi" now', project)

    assert '--title "[CLAUDE] say \\"hi\\" now"' in call["ArgText"]
