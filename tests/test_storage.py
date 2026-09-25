"""Tests for storage and locking mechanisms."""

from claude_session_restore.storage import (
    ENV_REGISTRY_PATH,
    FileLock,
    get_default_registry_path,
)


def test_storage_save_and_load(storage, sample_entry):
    # Initial load returns empty registry
    reg_init = storage.load()
    assert len(reg_init.sessions) == 0

    # Save data
    reg_init.sessions[sample_entry.session_id] = sample_entry
    storage.save(reg_init)

    # Load again
    reloaded = storage.load()
    assert len(reloaded.sessions) == 1
    assert sample_entry.session_id in reloaded.sessions
    entry = reloaded.sessions[sample_entry.session_id]
    assert entry.name == sample_entry.name


def test_storage_corrupted_file_recovery(storage, temp_registry_file):
    # Write invalid JSON
    temp_registry_file.write_text("{{INVALID JSON!!!", encoding="utf-8")

    # Load should safely recover, produce backup, and return empty registry
    data = storage.load()
    assert len(data.sessions) == 0

    # Ensure corrupt file was moved to backup
    backups = list(temp_registry_file.parent.glob("*.corrupt.*.json"))
    assert len(backups) == 1


def test_file_lock_acquire_and_release(temp_dir):
    lock_file = temp_dir / "test.lock"
    lock = FileLock(lock_file)
    lock.acquire()
    assert lock._fd is not None
    lock.release()
    assert lock._fd is None


def test_env_path_override(temp_dir, monkeypatch):
    custom_path = temp_dir / "custom-path" / "sessions.json"
    monkeypatch.setenv(ENV_REGISTRY_PATH, str(custom_path))

    path = get_default_registry_path()
    assert path == custom_path.resolve()
