"""scripts/Restore-AgentSessions.ps1 must hand real, correctly quoted arguments to Start-Process.

Start-Process joins an -ArgumentList array with plain spaces and does not
quote the elements, so a title or directory containing a space was split into
several arguments (Windows Terminal then ran the words of the session name as
a command). Start-Process is shadowed by a recording function in these tests,
so no terminal window is started. A Windows-only test executes the captured
batch script with cmd.exe /c and a harmless Python argument probe.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import _psparse
import pytest
from _cmdsim import win_argv

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "Restore-AgentSessions.ps1"

pytestmark = pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 not available")


@pytest.mark.parametrize("name", [";one;;two;", r"back\;slash 中文"])
def test_wt_semicolons_are_escaped_in_every_argument(tmp_path, name):
    project = tmp_path / "; project;"
    project.mkdir()
    (call,) = _run(
        tmp_path, "wt", name, project,
        custom="Write-Output {cwd}; Write-Output {name}",
    )
    argv = win_argv(call["ArgText"])
    assert argv[argv.index("-d") + 1] == str(project).replace(";", r"\;")
    assert argv[argv.index("--title") + 1] == f"[CLAUDE] {name}".replace(";", r"\;")
    expected = f"Write-Output '{project}'; Write-Output '{name}'"
    assert argv[-1] == expected.replace(";", r"\;")
    assert argv[-1].replace(r"\;", ";") == expected


RECORDER = r"""
$global:calls = @()
function Start-Process {
    [CmdletBinding()]
    param([Parameter(Position = 0)]$FilePath, [object]$ArgumentList, $WorkingDirectory)
    # Same semantics as the real cmdlet: an array is joined with single spaces.
    $text = if ($ArgumentList -is [array]) { $ArgumentList -join ' ' } else { [string]$ArgumentList }
    $scriptPath = ''
    $scriptText = ''
    if ($FilePath -eq 'cmd.exe' -and $text -match 'call "([^"]+)"$') {
        $scriptPath = $Matches[1]
        $scriptText = [System.IO.File]::ReadAllText($scriptPath)
    }
    $global:calls += [pscustomobject]@{
        FilePath = [string]$FilePath; ArgText = $text
        ScriptPath = $scriptPath; ScriptText = $scriptText
    }
    if ('{fail}' -eq 'True') { throw 'simulated launch failure' }
}
try {
    & '{script}' -Terminal {terminal} -RegistryFile '{registry}' -DelayMs 0 {dry_run} *> $null
} catch {
    if ('{fail}' -ne 'True') { throw }
}
ConvertTo-Json -InputObject @($global:calls) -Compress | Set-Content -LiteralPath '{out}' -Encoding utf8
"""


def _run(
    tmp_path: Path, terminal: str, name: str, project: Path, *,
    custom: str | None = None, agent: str = "claude", dry_run: bool = False,
    fail: bool = False, session_id: str = "s-1", cwd: str | None = None,
    expect_calls: int | None = None,
) -> list[dict[str, str]]:
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "version": 1,
                "sessions": {
                    "s-1": {
                        "session_id": session_id,
                        "name": name,
                        "cwd": cwd if cwd is not None else str(project),
                        "agent": agent,
                        "custom_resume_cmd": custom,
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
        RECORDER.replace("{script}", str(SCRIPT).replace("'", "''"))
        .replace("{terminal}", terminal)
        .replace("{registry}", str(registry).replace("'", "''"))
        .replace("{out}", str(out).replace("'", "''"))
        .replace("{fail}", str(fail))
        .replace("{dry_run}", "-DryRun" if dry_run else "")
    )
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    proc = subprocess.run(
        ["pwsh", "-NoProfile", "-NonInteractive", "-Command", code],
        capture_output=True, text=True, timeout=120, creationflags=flags,
        env={**os.environ, "TEMP": str(tmp_path), "TMP": str(tmp_path), "TMPDIR": str(tmp_path)},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    calls = json.loads(out.read_text("utf-8-sig"))
    if expect_calls is None:
        expect_calls = 0 if dry_run else 1
    assert isinstance(calls, list) and len(calls) == expect_calls
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


@pytest.mark.parametrize("agent", ["claude", "codex", "cursor", "antigravity", "hermes"])
def test_cmd_uses_temp_batch_not_inline_session_values(tmp_path, agent):
    from agent_session_restore.launcher import SessionLauncher
    from agent_session_restore.models import SessionEntry

    project = tmp_path / "R&D %PATH% ! 中文 (x)"
    project.mkdir()
    name = 'x" & echo INJECTED & "%PATH% ^ ! 測試'
    (call,) = _run(tmp_path, "cmd", name, project, agent=agent)
    assert call["ScriptPath"], call["ArgText"]
    script = Path(call["ScriptPath"])
    try:
        assert call["FilePath"] == "cmd.exe"
        assert call["ArgText"] == f'/k call "{script}"'
        assert script.parent == tmp_path
        assert script.suffix == ".cmd"
        for value in ("s-1", name, str(project)):
            assert value not in call["ArgText"]
        entry = SessionEntry(session_id="s-1", name=name, cwd=str(project), agent=agent)
        expected = SessionLauncher.build_cmd_script(entry)
        # Title text is escaped; compare command body separately.
        assert call["ScriptText"].splitlines()[:4] == expected.splitlines()[:4]
        assert call["ScriptText"].splitlines()[5:] == expected.splitlines()[5:]
        assert script.read_bytes().startswith(b"@echo off\r\nchcp 65001")
    finally:
        script.unlink(missing_ok=True)


@pytest.mark.skipif(sys.platform != "win32", reason="requires real cmd.exe")
def test_cmd_real_batch_preserves_custom_arguments_and_deletes_itself(tmp_path):
    from agent_session_restore.quoting import quote_batch_text

    project = tmp_path / "R&D %PATH% ! 中文 (x)"
    project.mkdir()
    name = 'x" & echo INJECTED & "%PATH% ^ ! 測試 a' + chr(92)
    probe = "import sys,json,os; print(json.dumps([sys.argv[1:],os.getcwd()]))"
    custom = f'"{sys.executable}" -c "{probe}" {{name}} {{cwd}} {{id}} {{session_id}}'
    odd_temp = tmp_path / "A&B C"
    odd_temp.mkdir()
    (call,) = _run(odd_temp, "cmd", name, project, custom=custom)
    assert call["ScriptPath"], call["ArgText"]
    script = Path(call["ScriptPath"])
    try:
        assert call["ScriptText"].splitlines()[4] == "title " + quote_batch_text("[CLAUDE] " + name)
        # Use the actual /k command text, replacing only /k with /c to avoid a window.
        run = subprocess.run(
            "cmd.exe " + call["ArgText"].replace("/k", "/c", 1),
            capture_output=True, stdin=subprocess.DEVNULL, timeout=60,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert run.stderr == b""
        args, cwd = json.loads(run.stdout.decode("utf-8").splitlines()[-1])
        assert args == [name, str(project), "s-1", "s-1"]
        assert cwd == str(project)
        assert not script.exists()
    finally:
        script.unlink(missing_ok=True)


def test_cmd_dry_run_creates_no_temp_script(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    assert _run(tmp_path, "cmd", "preview", project, dry_run=True) == []
    assert not list(tmp_path.glob("*.cmd"))


def test_cmd_launch_failure_cleans_temp_script(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (call,) = _run(tmp_path, "cmd", "failure", project, fail=True)
    assert call["ScriptText"]
    assert not Path(call["ScriptPath"]).exists()
    assert not list(tmp_path.glob("*.cmd"))


# A batch line ends at CR or LF, so any control character that reaches the
# script from the registry can start a new command line. The Python models
# strip them while building a SessionEntry; this script reads the JSON itself
# and must apply the same rules.
_PLAIN_CMD_LINE_COUNT = 9
_INJECTED_NAME = "x\r\ncalc.exe\r\nrem"


def _assert_no_injected_line(script_text: str) -> None:
    lines = script_text.splitlines()
    assert len(lines) == _PLAIN_CMD_LINE_COUNT, lines
    assert not any(line.strip().lower() == "calc.exe" for line in lines), lines


def test_cmd_name_with_crlf_cannot_start_a_batch_line(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()

    (call,) = _run(tmp_path, "cmd", _INJECTED_NAME, project)

    script = Path(call["ScriptPath"])
    try:
        _assert_no_injected_line(call["ScriptText"])
        assert call["ScriptText"].splitlines()[4] == "title [CLAUDE] xcalc.exerem"
    finally:
        script.unlink(missing_ok=True)


def test_cmd_name_with_crlf_in_custom_placeholder_stays_on_one_line(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()

    (call,) = _run(tmp_path, "cmd", _INJECTED_NAME, project, custom='tool --label "{name}"')

    script = Path(call["ScriptPath"])
    try:
        _assert_no_injected_line(call["ScriptText"])
    finally:
        script.unlink(missing_ok=True)


def test_cmd_agent_with_embedded_newline_cannot_start_a_batch_line(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()

    (call,) = _run(tmp_path, "cmd", "plain", project, agent="claude\r\ncalc.exe")

    script = Path(call["ScriptPath"])
    try:
        _assert_no_injected_line(call["ScriptText"])
    finally:
        script.unlink(missing_ok=True)


def test_name_of_only_control_characters_falls_back_to_directory_name(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()

    (call,) = _run(tmp_path, "cmd", "\r\n\x01", project)

    script = Path(call["ScriptPath"])
    try:
        assert call["ScriptText"].splitlines()[4] == "title [CLAUDE] proj"
    finally:
        script.unlink(missing_ok=True)


@pytest.mark.parametrize("terminal", ["cmd", "wt", "pwsh"])
def test_cwd_with_control_character_is_skipped_not_launched(tmp_path, terminal):
    project = tmp_path / "proj"
    project.mkdir()

    _run(tmp_path, terminal, "plain", project, cwd=str(project) + "\r\ncalc.exe", expect_calls=0)

    assert not list(tmp_path.glob("*.cmd"))


@pytest.mark.parametrize("terminal", ["cmd", "wt", "pwsh"])
def test_session_id_with_trailing_newline_is_rejected(tmp_path, terminal):
    project = tmp_path / "proj"
    project.mkdir()

    _run(tmp_path, terminal, "plain", project, session_id="s-1\n", expect_calls=0)

    assert not list(tmp_path.glob("*.cmd"))


@pytest.mark.parametrize("quote", _psparse.SINGLE_QUOTES)
@pytest.mark.parametrize("terminal", ["pwsh", "wt"])
def test_powershell_terminals_keep_unicode_quote_payloads_inside_string_literals(
    tmp_path, terminal, quote
):
    # PowerShell treats U+2018, U+2019, U+201A and U+201B as single quotes
    # too; ConvertTo-PSLiteral must double all of them, not just ASCII '.
    payload = f"x{quote}; calc.exe; {quote}y"
    project = tmp_path / payload
    project.mkdir()

    (call,) = _run(tmp_path, terminal, payload, project, custom="tool {name} {cwd} {id}")

    match = re.search(r'-Command "(.*)"$', call["ArgText"])
    assert match, call["ArgText"]
    command_text = match.group(1).replace('\\"', '"')
    names = _psparse.command_names(command_text)
    assert "calc.exe" not in names
    assert names.count("tool") == 1
