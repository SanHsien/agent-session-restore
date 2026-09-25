"""Tests for session registry business logic."""

from datetime import datetime, timedelta, timezone

from claude_session_restore.models import SessionStatus


def test_registry_register_and_get(registry, temp_dir):
    entry = registry.register(
        session_id="sess-001",
        name="web-ui",
        cwd=str(temp_dir),
        status=SessionStatus.ACTIVE.value,
    )
    assert entry.session_id == "sess-001"
    assert entry.name == "web-ui"

    fetched = registry.get("sess-001")
    assert fetched is not None
    assert fetched.name == "web-ui"
    assert fetched.is_active is True


def test_registry_register_updates_existing(registry, temp_dir):
    registry.register(session_id="sess-002", name="initial-name", cwd=str(temp_dir))
    updated = registry.register(session_id="sess-002", name="updated-name", cwd=str(temp_dir))

    assert updated.name == "updated-name"
    assert len(registry.list_sessions()) == 1


def test_registry_unregister_soft_and_hard(registry, temp_dir):
    registry.register(session_id="sess-003", name="temp-session", cwd=str(temp_dir))

    # Soft close
    assert registry.unregister("sess-003", hard_delete=False) is True
    closed_entry = registry.get("sess-003")
    assert closed_entry is not None
    assert closed_entry.status == SessionStatus.CLOSED.value

    # Hard delete
    assert registry.unregister("sess-003", hard_delete=True) is True
    assert registry.get("sess-003") is None


def test_registry_list_sessions_filter_and_sort(registry, temp_dir):
    dir_a = temp_dir / "project-a"
    dir_b = temp_dir / "project-b"
    dir_a.mkdir()
    dir_b.mkdir()

    registry.register(session_id="s1", name="sess1", cwd=str(dir_a), status="active")
    registry.register(session_id="s2", name="sess2", cwd=str(dir_b), status="closed")

    # All sessions
    all_sessions = registry.list_sessions(active_only=False)
    assert len(all_sessions) == 2

    # Active only
    active_sessions = registry.list_sessions(active_only=True)
    assert len(active_sessions) == 1
    assert active_sessions[0].session_id == "s1"

    # Filter by CWD
    filtered = registry.list_sessions(cwd_filter=str(dir_a))
    assert len(filtered) == 1
    assert filtered[0].session_id == "s1"


def test_registry_prune_stale(registry, temp_dir):
    # Register an old closed session
    registry.register(session_id="old-closed", name="old", cwd=str(temp_dir), status="closed")
    old_time = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    reg_data = registry.storage.load()
    reg_data.sessions["old-closed"].updated_at = old_time
    registry.storage.save(reg_data)

    # Prune sessions older than 14 days
    pruned = registry.prune(max_age_days=14, keep_active=True)
    assert pruned == 1
    assert registry.get("old-closed") is None


def test_registry_export_markdown(registry, temp_dir):
    registry.register(session_id="sess-export", name="export-test", cwd=str(temp_dir), agent="codex")
    md = registry.export_markdown()
    assert "Sessions" in md
    assert "export-test" in md
    assert "sess-export" in md
    assert "CODEX" in md
