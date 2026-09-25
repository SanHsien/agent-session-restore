"""Pytest fixtures for claude-session-restore tests."""

from __future__ import annotations

import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest

from claude_session_restore.models import SessionEntry
from claude_session_restore.registry import SessionRegistry
from claude_session_restore.storage import SessionStorage


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    """Provide a temporary directory that is automatically cleaned up."""
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


@pytest.fixture
def temp_registry_file(temp_dir: Path) -> Path:
    """Provide a path to a temporary registry file."""
    return temp_dir / "test-claude-sessions.json"


@pytest.fixture
def storage(temp_registry_file: Path) -> SessionStorage:
    """Provide an isolated SessionStorage instance."""
    return SessionStorage(file_path=temp_registry_file)


@pytest.fixture
def registry(storage: SessionStorage) -> SessionRegistry:
    """Provide an isolated SessionRegistry instance."""
    return SessionRegistry(storage=storage)


@pytest.fixture
def sample_entry(temp_dir: Path) -> SessionEntry:
    """Provide a valid test SessionEntry pointing to an existing directory."""
    project_dir = temp_dir / "my-project"
    project_dir.mkdir(parents=True, exist_ok=True)
    return SessionEntry(
        session_id="test-session-1234-abcd",
        name="my-feature-branch",
        cwd=str(project_dir),
        status="active",
        git_branch="feature/login",
    )
