"""Tests for data models in claude_session_restore."""

import pytest

from claude_session_restore.models import (
    SessionEntry,
    SessionRegistryData,
    SessionStatus,
)


def test_session_entry_creation_and_defaults(temp_dir):
    entry = SessionEntry(
        session_id="uuid-1111-2222",
        name="auth-service",
        cwd=str(temp_dir),
    )
    assert entry.session_id == "uuid-1111-2222"
    assert entry.name == "auth-service"
    assert entry.is_active is True
    assert entry.cwd_exists is True
    assert entry.status == SessionStatus.ACTIVE.value
    assert entry.created_at is not None
    assert entry.updated_at is not None


def test_session_entry_auto_naming(temp_dir):
    project_dir = temp_dir / "my-cool-app"
    project_dir.mkdir()
    entry = SessionEntry(
        session_id="abc-9999-0000",
        name="",
        cwd=str(project_dir),
    )
    assert "my-cool-app" in entry.name
    assert "abc-9999" in entry.name


def test_session_entry_validation_empty_id(temp_dir):
    with pytest.raises(ValueError, match="session_id must not be empty"):
        SessionEntry(session_id="", name="test", cwd=str(temp_dir))


def test_session_entry_serialization(temp_dir):
    entry = SessionEntry(
        session_id="uuid-test",
        name="test-name",
        cwd=str(temp_dir),
        status="closed",
        git_branch="main",
        pid=1234,
        notes="Important task",
    )
    d = entry.to_dict()
    assert d["session_id"] == "uuid-test"
    assert d["status"] == "closed"
    assert d["git_branch"] == "main"
    assert d["pid"] == 1234
    assert d["notes"] == "Important task"

    rebuilt = SessionEntry.from_dict(d)
    assert rebuilt.session_id == entry.session_id
    assert rebuilt.status == "closed"
    assert rebuilt.pid == 1234
    assert rebuilt.notes == "Important task"


def test_session_registry_data_dict_roundtrip(sample_entry):
    reg = SessionRegistryData()
    reg.sessions[sample_entry.session_id] = sample_entry

    as_dict = reg.to_dict()
    assert as_dict["version"] == 1
    assert sample_entry.session_id in as_dict["sessions"]

    reloaded = SessionRegistryData.from_dict(as_dict)
    assert len(reloaded.sessions) == 1
    loaded_entry = reloaded.sessions[sample_entry.session_id]
    assert loaded_entry.name == sample_entry.name
    assert loaded_entry.cwd == sample_entry.cwd
