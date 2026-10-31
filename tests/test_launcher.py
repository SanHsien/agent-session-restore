"""Tests for session launcher."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from _cmdsim import parse_passes, win_argv

from agent_session_restore.launcher import CMD_SCRIPT_PLACEHOLDER, SessionLauncher
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
    assert "/k" in cmd
    assert cmd[-1] == CMD_SCRIPT_PLACEHOLDER


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

    title_line = SessionLauncher.build_cmd_script(entry).split("\r\n")[4]
    # The script's title line is parsed once by cmd.exe: no & may be live, and
    # the whole malicious name must come out as plain title text.
    delivered, live = parse_passes(title_line, 1)
    assert live == []
    assert delivered == 'title CODEX: x" & calc & "'


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


def _script_lines(entry: SessionEntry) -> list[str]:
    return SessionLauncher.build_cmd_script(entry).split("\r\n")


def test_cmd_backend_keeps_every_session_value_out_of_the_command_line(temp_dir):
    # The old design embedded id/cwd/name in the `/c start ... cmd /k <text>`
    # string, which cmd.exe parses twice (provably airtight escaping was
    # impossible). Now every session value lives only in the temp script.
    project = temp_dir / "R&D %PATH% !x"
    project.mkdir()
    entry = SessionEntry(
        session_id="ok-id-5", name='x" & calc & "', cwd=str(project), agent="cursor"
    )
    script = SessionLauncher(backend="cmd").write_cmd_script(entry)
    try:
        cmd = SessionLauncher(backend="cmd").build_resume_command(entry, script)
    finally:
        script.unlink(missing_ok=True)

    assert cmd[:6] == ["cmd.exe", "/c", "start", '"asr"', "cmd", "/k"]
    # `start` treats a quoted first argument as the window title; an unquoted
    # one would be executed as a program.
    assert cmd[3].startswith('"')
    joined = " ".join(cmd)
    for value in (entry.session_id, entry.name, entry.cwd, "calc", "R&D", "CURSOR"):
        assert value not in joined
    assert script.name in joined


def test_cmd_backend_dry_run_does_not_write_a_script(temp_dir):
    entry = SessionEntry(session_id="ok-id-7", name="n", cwd=str(temp_dir), agent="claude")
    before = set(Path(tempfile.gettempdir()).glob("asr-*.cmd"))
    cmd = SessionLauncher(backend="cmd").build_resume_command(entry)
    assert cmd[-1] == CMD_SCRIPT_PLACEHOLDER
    assert set(Path(tempfile.gettempdir()).glob("asr-*.cmd")) == before


def test_cmd_script_values_survive_one_cmd_parse_as_single_arguments(temp_dir):
    # Model check (pure string work, runs on every OS): after the single
    # cmd.exe parse a script line gets, no & | < > is live and the program
    # receives each value as exactly one argument.
    project = temp_dir / "R&D"
    entry = SessionEntry(session_id="ok-id-5", name="n", cwd=str(project), agent="cursor")
    agent_line = _script_lines(entry)[6]

    delivered, live = parse_passes(agent_line, 1)
    assert live == []
    assert win_argv(delivered) == ["cursor", entry.cwd]


def test_cmd_script_title_and_custom_template_values_are_inert(temp_dir):
    entry = SessionEntry(
        session_id="ok-id-6",
        name="x&calc",
        cwd=str(temp_dir),
        agent="custom",
        custom_resume_cmd="myagent --name {name}",
    )
    lines = _script_lines(entry)

    delivered_title, live_title = parse_passes(lines[4], 1)
    assert live_title == []
    assert delivered_title == "title CUSTOM: x&calc"

    delivered, live = parse_passes(lines[6], 1)
    assert live == []
    assert win_argv(delivered) == ["myagent", "--name", "x&calc"]


def test_cmd_script_deletes_itself_first_and_ends_the_batch_quietly(temp_dir):
    lines = _script_lines(
        SessionEntry(session_id="ok-id-8", name="n", cwd=str(temp_dir), agent="claude")
    )
    # One parenthesized block: cmd.exe reads it whole, so the delete is safe,
    # and `(goto) 2>nul` ends the batch without "batch file not found".
    assert lines[2] == "("
    assert lines[3].startswith('del "%~f0"')
    assert lines[-3:] == ["(goto) 2>nul", ")", ""]


def test_cmd_backend_passes_one_raw_command_line_not_an_argv_list(temp_dir):
    # On Windows, Popen(list) re-quotes every element with list2cmdline, which
    # backslash-escapes the quotes around a script path containing a space
    # (cmd.exe does not understand that). The cmd backend must therefore hand
    # Popen the exact string it built. Popen is mocked: nothing is started.
    project_dir = temp_dir / "proj"
    project_dir.mkdir()
    entry = SessionEntry(session_id="ok-id-4", name="plain", cwd=str(project_dir), agent="cursor")
    launcher = SessionLauncher(backend="cmd")

    with patch("agent_session_restore.launcher.subprocess.Popen") as mock_popen:
        res = launcher.launch_session(entry)

    script = Path(res.command[-1].strip('"'))
    try:
        assert res.success is True
        popen_args = mock_popen.call_args.args[0]
        assert isinstance(popen_args, str)
        assert popen_args == " ".join(res.command)
        # Written but not yet executed, so the script still exists here.
        assert script.is_file()
        assert "CURSOR: plain" in script.read_text(encoding="utf-8")
    finally:
        script.unlink(missing_ok=True)


def test_cmd_backend_removes_the_script_when_the_launch_fails(temp_dir):
    entry = SessionEntry(session_id="ok-id-9", name="n", cwd=str(temp_dir), agent="claude")
    before = set(Path(tempfile.gettempdir()).glob("asr-*.cmd"))

    with patch("agent_session_restore.launcher.subprocess.Popen", side_effect=OSError("boom")):
        res = SessionLauncher(backend="cmd").launch_session(entry)

    assert res.success is False
    assert res.error_message == "boom"
    assert set(Path(tempfile.gettempdir()).glob("asr-*.cmd")) == before


@pytest.mark.skipif(os.name != "nt", reason="runs the generated script with the real cmd.exe")
def test_cmd_script_with_real_cmd_delivers_values_verbatim_and_deletes_itself(temp_dir):
    # Real cmd.exe, no window (`/c`, captured output): the program started by
    # the script must receive the exact original name and directory even when
    # they contain % ! & ^ " ( ) and non-ASCII text, and the script must be gone.
    project = temp_dir / "R&D %PATH% ! 中文 (x)"
    project.mkdir()
    name = 'x" & calc & "%PATH% ^ ! 測試 a' + chr(92)
    probe = "import sys, json; print(json.dumps(sys.argv[1:], ensure_ascii=True))"
    entry = SessionEntry(
        session_id="ok-id-10",
        name=name,
        cwd=str(project),
        agent="custom",
        custom_resume_cmd=f'"{sys.executable}" -c "{probe}" {{name}} {{cwd}} {{id}}',
    )
    script = SessionLauncher(backend="cmd").write_cmd_script(entry)
    try:
        run = subprocess.run(
            ["cmd.exe", "/c", str(script)],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            cwd=str(project),
            timeout=60,
            check=False,
        )
    finally:
        was_deleted = not script.exists()
        script.unlink(missing_ok=True)

    # (`(goto) 2>nul` leaves ERRORLEVEL 1, so the exit code is not checked.)
    assert run.stderr == b""
    assert json.loads(run.stdout.decode("utf-8").splitlines()[-1]) == [
        entry.name,
        entry.cwd,
        "ok-id-10",
    ]
    assert was_deleted


@pytest.mark.skipif(os.name != "nt", reason="runs the real cmd.exe `start`")
def test_cmd_backend_start_treats_quoted_asr_as_title_not_program(temp_dir):
    # Real cmd.exe, no window (`start /b /wait`). With an unquoted `asr`, start
    # would run it as a program: a planted asr.cmd in the session directory
    # (Popen's cwd) would execute and the agent line would never run.
    project = temp_dir / "proj"
    project.mkdir()
    (project / "asr.cmd").write_text("@echo PLANTED\r\n", encoding="ascii")
    entry = SessionEntry(
        session_id="ok-id-11",
        name="n",
        cwd=str(project),
        agent="custom",
        custom_resume_cmd=f'"{sys.executable}" -c "print(chr(79)+chr(75))"',
    )
    launcher = SessionLauncher(backend="cmd")
    script = launcher.write_cmd_script(entry)
    try:
        cmd = launcher.build_resume_command(entry, script)
        cmd[4:4] = ["/b", "/wait"]  # keep it windowless; title stays cmd[3]
        run = subprocess.run(
            " ".join(cmd),
            capture_output=True,
            stdin=subprocess.DEVNULL,
            cwd=str(project),
            timeout=60,
            check=False,
        )
    finally:
        was_deleted = not script.exists()
        script.unlink(missing_ok=True)

    out = run.stdout.decode("utf-8", "replace")
    assert "PLANTED" not in out
    assert "OK" in out
    assert was_deleted


@pytest.mark.skipif(os.name != "nt", reason="runs the real cmd.exe `start`")
def test_cmd_backend_starts_when_temp_path_has_space_and_cmd_metacharacters(temp_dir, monkeypatch):
    # `cmd /k "<quoted path>"` strips the outer quotes when the line has more
    # than two quotes, so a TEMP directory like `A&B C` was split at the `&`.
    # `call "<path>"` keeps it one token. Real cmd.exe, no window (`start /b /wait`).
    odd_temp = temp_dir / "A&B C"
    odd_temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(odd_temp))
    project = temp_dir / "proj"
    project.mkdir()
    entry = SessionEntry(
        session_id="ok-id-12",
        name="n",
        cwd=str(project),
        agent="custom",
        custom_resume_cmd=f'"{sys.executable}" -c "print(chr(79)+chr(75))"',
    )
    launcher = SessionLauncher(backend="cmd")
    script = launcher.write_cmd_script(entry)
    try:
        assert script.parent == odd_temp
        cmd = launcher.build_resume_command(entry, script)
        cmd[4:4] = ["/b", "/wait"]  # keep it windowless; title stays cmd[3]
        run = subprocess.run(
            " ".join(cmd),
            capture_output=True,
            stdin=subprocess.DEVNULL,
            cwd=str(project),
            timeout=60,
            check=False,
        )
    finally:
        was_deleted = not script.exists()
        script.unlink(missing_ok=True)

    assert "OK" in run.stdout.decode("utf-8", "replace")
    assert was_deleted
