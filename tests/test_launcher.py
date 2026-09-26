"""Tests for session launcher."""


from agent_session_restore.launcher import SessionLauncher
from agent_session_restore.models import SessionEntry


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


def test_build_resume_commands_all_agents(temp_dir):
    launcher = SessionLauncher(backend="wt")

    # Codex
    entry_codex = SessionEntry(session_id="codex-1", name="cx", cwd=str(temp_dir), agent="codex")
    cmd_codex = launcher.build_resume_command(entry_codex)
    assert "codex resume codex-1" in cmd_codex[-1]
    assert "CODEX: cx" in cmd_codex

    # Cursor
    entry_cursor = SessionEntry(session_id="cursor-1", name="cr", cwd=str(temp_dir), agent="cursor")
    cmd_cursor = launcher.build_resume_command(entry_cursor)
    # Compare against the resolved cwd (SessionEntry normalizes it via Path.resolve(),
    # which can expand short 8.3-style Windows paths, e.g. on GitHub Actions runners).
    assert f"cursor '{entry_cursor.cwd}'" in cmd_cursor[-1]
    assert "CURSOR: cr" in cmd_cursor

    # Antigravity
    entry_agy = SessionEntry(session_id="agy-1", name="ag", cwd=str(temp_dir), agent="antigravity")
    cmd_agy = launcher.build_resume_command(entry_agy)
    assert "agy resume agy-1" in cmd_agy[-1]
    assert "ANTIGRAVITY: ag" in cmd_agy

    # Hermes
    entry_hermes = SessionEntry(session_id="hermes-1", name="hm", cwd=str(temp_dir), agent="hermes")
    cmd_hermes = launcher.build_resume_command(entry_hermes)
    assert "hermes resume hermes-1" in cmd_hermes[-1]
    assert "HERMES: hm" in cmd_hermes

    # Custom
    entry_custom = SessionEntry(
        session_id="custom-1",
        name="cs",
        cwd=str(temp_dir),
        agent="custom",
        custom_resume_cmd="myagent start --task {id} --dir {cwd}",
    )
    cmd_custom = launcher.build_resume_command(entry_custom)
    assert f"myagent start --task custom-1 --dir {entry_custom.cwd}" in cmd_custom[-1]


def test_generate_powershell_script(sample_entry):
    launcher = SessionLauncher(backend="wt")
    script = launcher.generate_powershell_script([sample_entry])
    assert "Session Restore Script" in script
    assert sample_entry.session_id in script
    assert sample_entry.name in script

