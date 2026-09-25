"""Atomic and concurrency-safe persistence for Claude session registry."""

from __future__ import annotations

import contextlib
import json
import os
import sys
import time
import uuid
from collections.abc import Generator
from pathlib import Path

from claude_session_restore.models import SessionRegistryData

DEFAULT_REGISTRY_FILENAME = "claude-sessions.json"
ENV_REGISTRY_PATH = "CLAUDE_SESSION_REGISTRY_FILE"


def get_default_registry_path() -> Path:
    """Get the default path for the session registry file.

    Priority:
    1. Environment variable CLAUDE_SESSION_REGISTRY_FILE
    2. ~/.claude/claude-sessions.json
    """
    env_override = os.environ.get(ENV_REGISTRY_PATH)
    if env_override and env_override.strip():
        return Path(env_override.strip()).resolve()

    claude_dir = Path.home() / ".claude"
    return claude_dir / DEFAULT_REGISTRY_FILENAME


class FileLock:
    """Cross-platform advisory file lock using msvcrt on Windows and fcntl on POSIX."""

    def __init__(self, lock_path: Path, timeout_seconds: float = 5.0) -> None:
        self.lock_path = lock_path
        self.timeout_seconds = timeout_seconds
        self._fd: int | None = None

    def acquire(self) -> None:
        start_time = time.monotonic()
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)

        while True:
            try:
                # Open or create lock file with read/write access
                self._fd = os.open(str(self.lock_path), os.O_RDWR | os.O_CREAT | os.O_TRUNC)

                if os.name == "nt":
                    import msvcrt

                    # LK_NBLCK: Non-blocking lock on 1 byte
                    msvcrt.locking(self._fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[attr-defined]

                return  # Lock acquired successfully
            except OSError:
                if self._fd is not None:
                    with contextlib.suppress(Exception):
                        os.close(self._fd)
                    self._fd = None

                if time.monotonic() - start_time >= self.timeout_seconds:
                    # Timeout reached; continue anyway to avoid deadlocking hooks
                    break
                time.sleep(0.05)

    def release(self) -> None:
        if self._fd is not None:
            try:
                if os.name == "nt":
                    import msvcrt

                    with contextlib.suppress(OSError, ValueError):
                        msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    with contextlib.suppress(OSError, ValueError):
                        fcntl.flock(self._fd, fcntl.LOCK_UN)  # type: ignore[attr-defined]

                os.close(self._fd)
            except Exception:
                pass
            finally:
                self._fd = None


class SessionStorage:
    """Handles thread-safe and process-safe reading/writing of the session registry."""

    def __init__(self, file_path: Path | None = None) -> None:
        self.file_path = (file_path or get_default_registry_path()).resolve()
        self.lock_path = self.file_path.with_name(f"{self.file_path.name}.lock")

    @contextlib.contextmanager
    def _locked(self) -> Generator[None, None, None]:
        lock = FileLock(self.lock_path)
        lock.acquire()
        try:
            yield
        finally:
            lock.release()

    def load(self) -> SessionRegistryData:
        """Load session registry from disk. Returns empty registry if file doesn't exist."""
        with self._locked():
            if not self.file_path.exists():
                return SessionRegistryData()

            try:
                content = self.file_path.read_text(encoding="utf-8")
                if not content.strip():
                    return SessionRegistryData()
                data = json.loads(content)
                if not isinstance(data, dict):
                    return SessionRegistryData()
                return SessionRegistryData.from_dict(data)
            except Exception as e:
                # If corrupted, make a backup and start fresh to avoid total failure
                sys.stderr.write(f"[claude-session-restore] Warning: Corrupt registry: {e}. Backing up.\n")
                with contextlib.suppress(Exception):
                    backup_path = self.file_path.with_suffix(f".corrupt.{int(time.time())}.json")
                    self.file_path.rename(backup_path)
                return SessionRegistryData()

    def save(self, registry_data: SessionRegistryData) -> None:
        """Atomically persist session registry to disk using temporary file replacement."""
        with self._locked():
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            temp_file = self.file_path.with_name(
                f".{self.file_path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}"
            )
            raw_json = json.dumps(registry_data.to_dict(), indent=2, ensure_ascii=False)

            try:
                temp_file.write_text(raw_json, encoding="utf-8")
                # Atomic file replacement
                os.replace(temp_file, self.file_path)
            except Exception:
                if temp_file.exists():
                    with contextlib.suppress(Exception):
                        temp_file.unlink()
                raise
