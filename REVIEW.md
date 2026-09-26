# Independent Review - agent-session-restore

Date: 2026-09-26
Reviewer environment: Windows 11 (host msi-P50), Python 3.13.14 via uv, repo at C:\GitHub\agent-session-restore, branch main.

## Environment / commands run

$ unset UV_PROJECT_ENVIRONMENT   # ensured repo's own .venv is used, not a shared one
$ uv sync --link-mode=copy
Resolved 19 packages ... Installed 14 packages ... + agent-session-restore==0.2.0 (from file:///C:/GitHub/agent-session-restore)

$ uv run pytest -v
32 passed in ~1.5-1.9s

$ uv run ruff check .
All checks passed!

$ uv run mypy src
Success: no issues found in 8 source files

$ uv run asr --version && uv run csr --version
asr 0.2.0
asr 0.2.0

uv run asr --help / uv run csr --help both list identical subcommands, confirmed working.

Hook scripts were also exercised end-to-end with CLAUDE_SESSION_REGISTRY_FILE pointed at a throwaway temp file (never touching the real ~/.claude data):

echo session-start payload | uv run python hooks/session-start.py
-> systemMessage: Session tracked by agent-session-restore: [CLAUDE] review-test [test-abc]
echo session-end payload | uv run python hooks/session-end.py
-> systemMessage: Session closed in agent-session-restore: [test-abc]

Registry JSON was written, updated, and closed correctly via atomic replace plus file lock.

## CI check (gh run list / gh run view, repo SanHsien/agent-session-restore)

Both pushed commits (678c9e4, 88aa6d2) had FAILING CI before this review:
- ubuntu-latest jobs (all 4 Python versions): failed at Mypy Type Check - 12 attr-defined / unused-ignore errors in storage.py, duplicated across agent_session_restore and claude_session_restore. Root cause: mypy platform-specific typeshed stubs for msvcrt/fcntl are gated by host OS; running mypy on Linux hid msvcrt real attributes while exposing fcntl, flipping which type: ignore comments were needed vs unused depending on host - a config bug, not a Windows-only limitation.
- windows-latest jobs (e.g. Python 3.12): passed lint/mypy but failed Pytest - test_build_resume_commands_all_agents asserted against the raw tmp_path string, while SessionEntry post-init normalizes cwd via Path resolve. On GitHub Windows runners the temp path short 8.3 form (RUNNER dot 1) resolves to a different long form (runneradmin), so the raw and resolved strings diverged - a genuine environment-dependent flaky assertion, not present on a normal dev machine.

Both are fixed in this review (see below); CI has not yet re-run against the pushed commit at review-writing time - GitHub Actions status should be checked again after push.

## Overall verdict

The tool is real, not a stub, for its primary use case: SessionStart/SessionEnd hooks for Claude Code actually write, update, and close entries in an atomically-locked JSON registry, and asr/csr actually list, restore, register, prune, and export sessions. Tests exercise real file-locking, atomic-replace, and CLI behavior, not mocked-out no-ops.

However, README's five-agent-fleet framing overstates automation: only Claude Code has a real hook integration (hooks/session-start.py, hooks/session-end.py, wired via Claude Code SessionStart/SessionEnd events). Codex, Cursor, Antigravity, and Hermes support is limited to (a) an --agent field accepted by csr register and (b) resume-command templates in launcher.py and Restore-AgentSessions.ps1. There is no code in this repo that automatically detects or registers a Codex/Cursor/Antigravity/Hermes session - a user (or that agent's own tooling, external to this repo) would have to call csr register --agent codex manually. This should be described as manual registration plus templated resume for four agents, automatic hook-based tracking for Claude Code only.

## Problems found and fixed

1. CI mypy failure (all ubuntu-latest jobs), cross-platform stub mismatch - pyproject.toml tool.mypy section (no platform pin) plus src/agent_session_restore/storage.py lines 52-86 (os.name == nt runtime check, not statically recognized by mypy). Fixed by pinning platform = win32 in tool.mypy (project is explicitly Windows-first) and switching the runtime check to sys.platform == win32, which mypy specially recognizes to prune the unreachable POSIX (fcntl) branch entirely instead of type-checking it against whichever OS mypy happens to run on. Verified locally with uv run mypy src (clean) and uv run mypy src --platform linux (still clean, config wins over host/flag in this mypy 2.3.1 build) - this should now also pass on ubuntu-latest. Removed the now-unnecessary fcntl entry from tool.mypy.overrides (mypy flagged it as an unused section).
2. CI pytest failure (windows-latest), path-normalization mismatch - tests/test_launcher.py test_build_resume_commands_all_agents compared against the raw tmp_path string instead of the entry's resolved cwd. Fixed by asserting against entry_cursor.cwd and entry_custom.cwd (post-normalization values) instead of the raw fixture path.
3. Rename leftovers (severity: low, cleanliness/maintenance risk) - the 2026-09-25 rename to agent-session-restore left a full duplicate old implementation behind src/claude_session_restore/ (cli.py, hooks.py, launcher.py, models.py, registry.py, storage.py). Only __init__.py had been turned into a compat proxy; the rest were untouched copies of the pre-rename code, silently doubling mypy/ruff's checked surface and the CI mypy error count in item 1. Deleted the six duplicate .py files, keeping only the __init__.py proxy (import claude_session_restore still works for back-compat, re-exporting from agent_session_restore). Also removed the now-broken/dead fallback except ImportError branch in hooks/session-start.py and hooks/session-end.py (unreachable in practice since agent_session_restore always ships in this repo). Also found and removed a byte-identical duplicate PowerShell script scripts/Restore-ClaudeSessions.ps1 (kept Restore-AgentSessions.ps1, whose own docstring header still said Restore-ClaudeSessions.ps1 - fixed). Updated remaining claude-session-restore mentions in README.md, docs/architecture.md, and user-facing systemMessage/log strings in src/agent_session_restore/hooks.py, storage.py, launcher.py, cli.py and tests/conftest.py docstrings.
4. Re-ran ruff check ., mypy src, pytest -v after all changes: all green (32 passed, 0 lint/type errors).

## Open items (owner decision needed, not fixed here)

- docs/architecture.md is still substantially stale: diagrams and prose describe the pre-multi-agent, single-Claude design (Claude Code Sessions with 19+ count, SessionRegistryManager, claude -r id only). Fixed the two most misleading literal leftovers (title, script filename) but did not rewrite the diagrams/prose for multi-agent - that is a real doc-content task, not a quick fix.
- RESOLVED (2026-09-26 follow-up, owner-approved): README is now split into README.md (Traditional Chinese) and README.en.md (English), cross-linked at top, with an explicit "Per-agent support level" table stating Claude Code = automatic hook tracking vs. Codex/Cursor/Antigravity/Hermes = manual csr register + templated resume commands, no auto-detection. No sponsor/self-promotion links were found in the repo (only CI/Python/License/Platform status badges, which were kept).
- Command-injection surface (design-level, not a quick fix): custom_resume_cmd, session name, and cwd in the registry are attacker- or user-controlled strings that flow unsanitized into shell command strings built for wt.exe, cmd.exe, and PowerShell launches (launcher.py build_resume_command, Restore-AgentSessions.ps1). In the current single-user, locally-trusted-registry model this requires the attacker to already have write access to ~/.claude/claude-sessions.json, so severity is low today, but if the registry is ever fed from a less-trusted source (a shared/synced file, or a future network-facing agent), this becomes a real injection vector. Worth a design note/quoting audit before that happens.
- CI re-verified green on GitHub Actions after pushing the fix commit (16da390): all 8 matrix jobs (windows-latest / ubuntu-latest x Python 3.10-3.13) passed, run 36219426861, https://github.com/SanHsien/agent-session-restore/actions/runs/36219426861.

## Files touched

pyproject.toml, src/agent_session_restore/cli.py, hooks.py, launcher.py, storage.py, src/claude_session_restore/ (6 files deleted), hooks/session-start.py, hooks/session-end.py, scripts/Restore-AgentSessions.ps1, scripts/Restore-ClaudeSessions.ps1 (deleted), tests/test_launcher.py, tests/conftest.py, README.md, README.en.md (added, 2026-09-26 follow-up), docs/architecture.md, this REVIEW.md.
