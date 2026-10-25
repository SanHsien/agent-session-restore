"""Tests for Claude Code hooks handling."""

import json

from agent_session_restore.hooks import (
    handle_session_end,
    handle_session_start,
    parse_hook_payload,
)
from agent_session_restore.models import SessionStatus


def test_parse_hook_payload_from_json():
    raw = json.dumps({"session_id": "test-123", "cwd": "C:/demo", "session_name": "worker"})
    parsed = parse_hook_payload(raw)
    assert parsed["session_id"] == "test-123"
    assert parsed["cwd"] == "C:/demo"
    assert parsed["session_name"] == "worker"


def test_parse_hook_payload_from_env(monkeypatch):
    monkeypatch.setenv("CLAUDE_SESSION_ID", "env-session-id")
    monkeypatch.setenv("CLAUDE_SESSION_NAME", "env-worker")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", "C:/env-project")

    parsed = parse_hook_payload("")
    assert parsed["session_id"] == "env-session-id"
    assert parsed["session_name"] == "env-worker"
    assert parsed["cwd"] == "C:/env-project"


def test_handle_session_start_success(registry, temp_dir):
    payload = {
        "session_id": "hook-sess-1",
        "name": "refactor-auth",
        "cwd": str(temp_dir),
    }
    result = handle_session_start(payload=payload, registry=registry)
    assert "refactor-auth" in result.get("systemMessage", "")

    entry = registry.get("hook-sess-1")
    assert entry is not None
    assert entry.name == "refactor-auth"
    assert entry.status == SessionStatus.ACTIVE.value


def test_handle_session_start_missing_id(registry, temp_dir):
    payload = {"name": "no-id", "cwd": str(temp_dir)}
    result = handle_session_start(payload=payload, registry=registry)
    assert "Warning: No session_id found" in result.get("systemMessage", "")


def test_handle_session_end(registry, temp_dir):
    # First start
    start_payload = {"session_id": "hook-sess-end", "name": "to-close", "cwd": str(temp_dir)}
    handle_session_start(payload=start_payload, registry=registry)

    # Then end
    end_payload = {"session_id": "hook-sess-end"}
    result = handle_session_end(payload=end_payload, registry=registry)
    assert "Session closed" in result.get("systemMessage", "")

    entry = registry.get("hook-sess-end")
    assert entry is not None
    assert entry.status == SessionStatus.CLOSED.value



def test_parse_hook_payload_reads_stdin_as_utf8_whatever_the_locale(monkeypatch):
    # Claude Code sends UTF-8 JSON. sys.stdin decodes with the locale code page
    # (cp950 on Traditional Chinese Windows), so non-ASCII names/paths came out
    # as mojibake, or the read failed and the hook fell back to an empty payload.
    import io
    import sys

    payload = {"session_id": "utf8-sess", "session_name": "專案修復", "cwd": "C:/專案/資料"}
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(raw), encoding="cp950"))

    parsed = parse_hook_payload()

    assert parsed == payload


def test_parse_hook_payload_accepts_utf8_bom_on_stdin(monkeypatch):
    import io
    import sys

    raw = b"\xef\xbb\xbf" + json.dumps({"session_id": "bom-sess"}).encode("utf-8")
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(raw), encoding="cp950"))

    assert parse_hook_payload()["session_id"] == "bom-sess"
