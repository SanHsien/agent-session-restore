"""Tests for CLI subcommands."""

import json

from agent_session_restore.cli import main


def test_cli_register_and_list(temp_registry_file, temp_dir, capsys):
    ret = main([
        "--file", str(temp_registry_file),
        "register",
        "--id", "cli-sess-1",
        "--name", "cli-worker",
        "--cwd", str(temp_dir),
        "--branch", "main",
    ])
    assert ret == 0

    # List JSON
    capsys.readouterr()  # flush stdout
    ret_list = main([
        "--file", str(temp_registry_file),
        "list",
        "--json",
    ])
    assert ret_list == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert len(data) == 1
    assert data[0]["name"] == "cli-worker"
    assert data[0]["session_id"] == "cli-sess-1"


def test_cli_restore_dry_run(temp_registry_file, temp_dir, capsys):
    main([
        "--file", str(temp_registry_file),
        "register",
        "--id", "cli-sess-restore",
        "--name", "restore-test",
        "--cwd", str(temp_dir),
    ])

    ret = main([
        "--file", str(temp_registry_file),
        "restore",
        "--dry-run",
    ])
    assert ret == 0
    captured = capsys.readouterr()
    assert "Dry Run" in captured.out
    assert "restore-test" in captured.out
    assert "cli-sess-restore" in captured.out


def test_cli_unregister(temp_registry_file, temp_dir):
    main([
        "--file", str(temp_registry_file),
        "register",
        "--id", "to-remove",
        "--cwd", str(temp_dir),
    ])
    ret = main([
        "--file", str(temp_registry_file),
        "unregister",
        "--id", "to-remove",
        "--hard",
    ])
    assert ret == 0


def test_cli_export(temp_registry_file, temp_dir, capsys):
    main([
        "--file", str(temp_registry_file),
        "register",
        "--id", "exp-1",
        "--name", "exporter",
        "--cwd", str(temp_dir),
    ])
    ret = main([
        "--file", str(temp_registry_file),
        "export",
        "--format", "md",
    ])
    assert ret == 0
    out = capsys.readouterr().out
    assert "Sessions" in out


def test_cli_agent_filtering(temp_registry_file, temp_dir, capsys):
    main([
        "--file", str(temp_registry_file),
        "register",
        "--id", "cx-1",
        "--name", "codex-task",
        "--agent", "codex",
        "--cwd", str(temp_dir),
    ])
    main([
        "--file", str(temp_registry_file),
        "register",
        "--id", "cl-1",
        "--name", "claude-task",
        "--agent", "claude",
        "--cwd", str(temp_dir),
    ])

    capsys.readouterr()
    main([
        "--file", str(temp_registry_file),
        "list",
        "--agent", "codex",
        "--json",
    ])
    captured = capsys.readouterr().out
    data = json.loads(captured)
    assert len(data) == 1
    assert data[0]["agent"] == "codex"


def test_cli_install_hooks(capsys):
    ret = main(["install-hooks"])
    assert ret == 0
    out = capsys.readouterr().out
    assert "SessionStart" in out
    assert "SessionEnd" in out



def _feed_stdin(monkeypatch, payload):
    import io
    import sys

    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8"))


def test_cli_hook_start_never_raises_on_invalid_payload(
    temp_registry_file, temp_dir, monkeypatch, capsys
):
    # `csr hook-start` is wired straight into Claude Code's SessionStart. A
    # session id with a space fails validation; that used to escape main() as a
    # traceback and a non-zero exit, unlike hooks/session-start.py which is
    # documented to always exit 0.
    _feed_stdin(monkeypatch, {"session_id": "bad id", "cwd": str(temp_dir)})

    ret = main(["--file", str(temp_registry_file), "hook-start"])

    assert ret == 0
    assert "systemMessage" in json.loads(capsys.readouterr().out)


def test_cli_hook_end_never_raises_on_unusable_registry(
    temp_registry_file, monkeypatch, capsys
):
    from pathlib import Path
    from unittest.mock import patch

    temp_registry_file.write_text("{}", encoding="utf-8")
    _feed_stdin(monkeypatch, {"session_id": "some-session"})

    with patch.object(Path, "read_text", side_effect=PermissionError("locked")):
        ret = main(["--file", str(temp_registry_file), "hook-end"])

    assert ret == 0
    assert "systemMessage" in json.loads(capsys.readouterr().out)


def test_cli_hook_output_is_ascii_so_the_console_code_page_cannot_garble_it(
    temp_registry_file, temp_dir, monkeypatch, capsys
):
    # Hook stdout is encoded with the console code page (cp950 would garble or
    # reject non-ASCII text). JSON escapes keep it lossless and always encodable.
    _feed_stdin(
        monkeypatch, {"session_id": "ascii-sess", "name": "專案", "cwd": str(temp_dir)}
    )

    ret = main(["--file", str(temp_registry_file), "hook-start"])

    out = capsys.readouterr().out
    assert ret == 0
    assert out.isascii()
    assert "專案" in json.loads(out)["systemMessage"]
