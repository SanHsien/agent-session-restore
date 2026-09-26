"""Tests for session launcher."""

from unittest.mock import patch

import pytest

from agent_session_restore.launcher import SessionLauncher
from agent_session_restore.models import SessionEntry


def test_build_resume_command_wt(sample_entry):
    launcher = SessionLauncher(backend="wt")
    cmd = launcher.build_resume_command(sample_entry)
    assert cmd[0] == "wt.exe"
    assert "new-tab" in cmd
    assert "-d" in cmd
    assert sample_entry.cwd in cmd
    assert f"'claude' '-r' '{sample_entry.session_id}'" in cmd[-1]


def test_build_resume_command_pwsh(sample_entry):
    launcher = SessionLauncher(backend="pwsh")
    cmd = launcher.build_resume_command(sample_entry)
    assert "pwsh" in cmd[0] or "powershell" in cmd[0]
    assert "-NoExit" in cmd
    assert f"'claude' '-r' '{sample_entry.session_id}'" in cmd[-1]


def test_build_resume_command_cmd(sample_entry):
    launcher = SessionLauncher(backend="cmd")
    cmd = launcher.build_resume_command(sample_entry)
    assert cmd[0] == "cmd.exe"
    assert "/c" in cmd
    assert "start" in cmd
    assert f"claude -r {sample_entry.session_id}" in cmd[-1]


def test_launch_nonexistent_directory(temp_dir):
    fake_entry = SessionEntry(
        session_id="fake-id",
        name="fake",
        cwd=str(temp_dir / "does-not-exist-dir"),
    )
    launcher = SessionLauncher(backend="print")
    res = launcher.launch_session(fake_entry)
    assert res.success is False
    assert "Directory does not exist" in (res.error_message or "")


def test_build_resume_commands_all_agents(temp_dir):
    launcher = SessionLauncher(backend="wt")

    # Codex
    entry_codex = SessionEntry(session_id="codex-1", name="cx", cwd=str(temp_dir), agent="codex")
    cmd_codex = launcher.build_resume_command(entry_codex)
    assert "'codex' 'resume' 'codex-1'" in cmd_codex[-1]
    assert "CODEX: cx" in cmd_codex

    # Cursor
    entry_cursor = SessionEntry(session_id="cursor-1", name="cr", cwd=str(temp_dir), agent="cursor")
    cmd_cursor = launcher.build_resume_command(entry_cursor)
    # Compare against the resolved cwd (SessionEntry normalizes it via Path.resolve(),
    # which can expand short 8.3-style Windows paths, e.g. on GitHub Actions runners).
    assert f"'cursor' '{entry_cursor.cwd}'" in cmd_cursor[-1]
    assert "CURSOR: cr" in cmd_cursor

    # Antigravity
    entry_agy = SessionEntry(session_id="agy-1", name="ag", cwd=str(temp_dir), agent="antigravity")
    cmd_agy = launcher.build_resume_command(entry_agy)
    assert "'agy' 'resume' 'agy-1'" in cmd_agy[-1]
    assert "ANTIGRAVITY: ag" in cmd_agy

    # Hermes
    entry_hermes = SessionEntry(session_id="hermes-1", name="hm", cwd=str(temp_dir), agent="hermes")
    cmd_hermes = launcher.build_resume_command(entry_hermes)
    assert "'hermes' 'resume' 'hermes-1'" in cmd_hermes[-1]
    assert "HERMES: hm" in cmd_hermes

    # Custom: placeholders are substituted with per-shell-quoted values, but the
    # template text itself is left exactly as authored (trusted, arbitrary command).
    entry_custom = SessionEntry(
        session_id="custom-1",
        name="cs",
        cwd=str(temp_dir),
        agent="custom",
        custom_resume_cmd="myagent start --task {id} --dir {cwd}",
    )
    cmd_custom = launcher.build_resume_command(entry_custom)
    assert f"myagent start --task 'custom-1' --dir '{entry_custom.cwd}'" in cmd_custom[-1]


def test_generate_powershell_script(sample_entry):
    launcher = SessionLauncher(backend="wt")
    script = launcher.generate_powershell_script([sample_entry])
    assert "Session Restore Script" in script
    assert sample_entry.session_id in script
    assert sample_entry.name in script


# --- Security: malicious values must be rejected at validation or safely
# quoted, never executed. subprocess is always mocked here; nothing is
# actually launched.


def test_malicious_session_id_is_rejected(temp_dir):
    with pytest.raises(ValueError):
        SessionEntry(session_id="test; rm -rf /", name="x", cwd=str(temp_dir))
    with pytest.raises(ValueError):
        SessionEntry(session_id="`calc`", name="x", cwd=str(temp_dir))


def test_malicious_cwd_is_rejected(temp_dir):
    base = str(temp_dir)
    for bad_cwd in (
        base + '_evil"dir',
        base + "_evil<dir>",
        base + "_evil|dir",
    ):
        with pytest.raises(ValueError):
            SessionEntry(session_id="ok-id", name="x", cwd=bad_cwd)


def test_malicious_name_is_quoted_not_executed(temp_dir):
    # This name is *not* rejected (names are display text, only control
    # characters are stripped) -- it must instead be rendered inert by
    # quoting wherever it is embedded in a command string.
    entry = SessionEntry(
        session_id="ok-id",
        name='x" & calc & "',
        cwd=str(temp_dir),
        agent="codex",
    )
    launcher = SessionLauncher(backend="pwsh")
    cmd = launcher.build_resume_command(entry)
    script = cmd[-1]
    # The window-title assignment must contain the whole malicious string as
    # one single-quoted PowerShell literal, not as bare, unquoted script text.
    assert "$host.UI.RawUI.WindowTitle = 'CODEX: x\" & calc & \"'" in script

    launcher_cmd = SessionLauncher(backend="cmd")
    cmd2 = launcher_cmd.build_resume_command(entry)
    title_token = cmd2[3]
    # Every cmd.exe metacharacter in the title must have been caret-escaped.
    assert "^&" in title_token
    assert '^"' in title_token


def test_malicious_cwd_characters_neutralized_by_quoting(temp_dir):
    # Characters like ; $( ) ` % are legal in a Windows path (so cwd
    # validation does not reject them) but must still be neutralized by
    # per-shell quoting wherever cwd is embedded in a command string.
    tricky_dir = temp_dir / "proj_$(calc)_%PATH%_;_`x`"
    tricky_dir.mkdir()
    entry = SessionEntry(session_id="ok-id-2", name="n", cwd=str(tricky_dir), agent="cursor")

    launcher = SessionLauncher(backend="pwsh")
    cmd = launcher.build_resume_command(entry)
    script = cmd[-1]
    assert f"Set-Location -LiteralPath '{entry.cwd}'" in script
    assert f"'cursor' '{entry.cwd}'" in script


def test_launch_session_never_uses_shell_true(temp_dir):
    project_dir = temp_dir / "proj"
    project_dir.mkdir()
    entry = SessionEntry(session_id="ok-id-3", name="n", cwd=str(project_dir), agent="claude")
    launcher = SessionLauncher(backend="wt")

    with patch("agent_session_restore.launcher.subprocess.Popen") as mock_popen:
        mock_popen.return_value = None
        res = launcher.launch_session(entry)

    assert res.success is True
    assert mock_popen.called
    _, kwargs = mock_popen.call_args
    # shell=True would make Windows re-parse the whole argv list through
    # cmd.exe/COMSPEC on top of any shell text this module already built and
    # quoted itself -- a double-parsing hazard this design avoids entirely.
    assert kwargs.get("shell", False) is False
