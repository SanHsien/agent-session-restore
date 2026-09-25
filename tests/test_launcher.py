"""Tests for session launcher."""


from claude_session_restore.launcher import SessionLauncher
from claude_session_restore.models import SessionEntry


def test_build_resume_command_wt(sample_entry):
    launcher = SessionLauncher(backend="wt")
    cmd = launcher.build_resume_command(sample_entry)
    assert cmd[0] == "wt.exe"
    assert "new-tab" in cmd
    assert "-d" in cmd
    assert sample_entry.cwd in cmd
    assert f"claude -r {sample_entry.session_id}" in cmd[-1]


def test_build_resume_command_pwsh(sample_entry):
    launcher = SessionLauncher(backend="pwsh")
    cmd = launcher.build_resume_command(sample_entry)
    assert "pwsh" in cmd[0] or "powershell" in cmd[0]
    assert "-NoExit" in cmd
    assert f"claude -r {sample_entry.session_id}" in cmd[-1]


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


def test_generate_powershell_script(sample_entry):
    launcher = SessionLauncher(backend="wt")
    script = launcher.generate_powershell_script([sample_entry])
    assert "Restore-ClaudeSessions" in script or "Claude Code Session Restore Script" in script
    assert sample_entry.session_id in script
    assert sample_entry.name in script
