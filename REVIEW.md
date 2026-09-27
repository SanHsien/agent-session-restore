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

- RESOLVED (2026-09-26 follow-up, owner-approved): docs/architecture.md was rewritten in Traditional Chinese to match the current code, citing actual module names (agent_session_restore.models/storage/registry/hooks/launcher/quoting/cli, hooks/session-start.py, hooks/session-end.py, scripts/Restore-AgentSessions.ps1). It now covers: the registry JSON format, the Claude Code hook flow (the only automatic path), the manual `csr register` path for Codex/Cursor/Antigravity/Hermes, the restore/launch flow including per-shell quoting, input validation rules, and the FileLock/atomic-write mechanism, all with ASCII diagrams.
- RESOLVED (2026-09-26 follow-up, owner-approved): README is now split into README.md (Traditional Chinese) and README.en.md (English), cross-linked at top, with an explicit "Per-agent support level" table stating Claude Code = automatic hook tracking vs. Codex/Cursor/Antigravity/Hermes = manual csr register + templated resume commands, no auto-detection. No sponsor/self-promotion links were found in the repo (only CI/Python/License/Platform status badges, which were kept).
- RESOLVED (2026-09-26 follow-up, owner-approved): the command-injection surface was fixed, not just documented.
  - New module `src/agent_session_restore/quoting.py`: `quote_powershell()` (single-quoted PowerShell literal, only the quote character itself needs escaping) and `quote_cmd_token()` (caret-escapes cmd.exe metacharacters `^ & | < > ( ) @ ! % "`, wraps in quotes when needed), plus `format_argv_powershell()`/`format_argv_cmd()` for whole argv lists.
  - `models.py` `SessionEntry.__post_init__` now validates `session_id` against a strict allowlist (`^[A-Za-z0-9_.:-]{1,128}$`, reject rather than escape) and rejects `cwd` values containing `< > " | ? *` or ASCII control characters (existence is deliberately NOT required, to avoid regressing `csr prune --missing-dirs` and the launcher's own missing-directory handling); `name` is stripped of control characters only (it is free-form display text, safety comes from quoting at the point of use, not input rejection).
  - `launcher.py` `build_resume_command()` now builds the built-in per-agent resume command as an argv list (`build_agent_argv`) and only ever embeds it into a shell command string through `quote_powershell`/`quote_cmd_token`/`format_argv_*`, dialect-matched to the backend (wt/pwsh/powershell use PowerShell quoting, cmd uses cmd quoting); `subprocess.Popen` no longer passes `shell=True` for the cmd backend (always `shell=False`, avoiding the double-parsing hazard of also routing an already-built argv list through COMSPEC). `custom_resume_cmd` stays intentionally arbitrary/trusted per your instructions, but its `{id}`/`{cwd}`/`{name}` placeholders are now substituted with per-shell-quoted values instead of raw string interpolation.
  - `scripts/Restore-AgentSessions.ps1` gained equivalent `ConvertTo-PSLiteral`/`ConvertTo-CmdSafe` helpers, a `$SafeIdPattern` check that skips (with a warning) any session whose id fails the same allowlist, and per-dialect command construction (`$agentCmdPs` for wt/pwsh, `$agentCmdCmd` for cmd). Fixing this also surfaced and fixed one pre-existing, unrelated PowerShell parser bug in the same file: `"...$RegistryFile: $_"` was invalid syntax (`:` immediately after a variable name is parsed as a drive/scope reference) — changed to `${RegistryFile}`.
  - Tests: `tests/test_quoting.py` (new, 9 tests) covers both quoting helpers directly. `tests/test_launcher.py` gained `test_malicious_session_id_is_rejected`, `test_malicious_cwd_is_rejected`, `test_malicious_name_is_quoted_not_executed` (payload `x" & calc & "`), `test_malicious_cwd_characters_neutralized_by_quoting` (payload containing `$()`, backticks, `%PATH%`, `;`), and `test_launch_session_never_uses_shell_true` (mocks `subprocess.Popen`, asserts `shell` is not `True`; nothing is actually launched in any test). All 47 tests pass (`uv run pytest -v`).
  - Manually verified with `pwsh`: `[System.Management.Automation.Language.Parser]::ParseFile()` confirms `Restore-AgentSessions.ps1` parses cleanly; ran it with `-DryRun` against a fake registry containing a malicious session id (`bad id; rm -rf`) and a malicious name (`x" & calc & "`) — the malicious id was rejected and skipped with a warning, the malicious name was safely previewed; the `ConvertTo-PSLiteral`/`ConvertTo-CmdSafe` functions were also unit-checked in isolation against a combined payload (`x" & calc & "; $(rm -rf /) `whoami``), producing fully inert single-quoted PowerShell output and caret-escaped cmd output respectively. Nothing was actually launched.
  - Residual limitation, documented rather than hidden: cmd.exe's own command-line re-parsing after `/c`/`/k` is a well-known, historically imperfect area (edge cases exist beyond caret-escaping's guarantees). The wt/pwsh/powershell backends rest on PowerShell single-quoted-literal semantics, which are simple and complete; the cmd backend is meaningfully hardened but not claimed to be provably airtight the way the PowerShell backends are. Recommend defaulting to the wt/pwsh backend where possible; a future improvement would route the cmd backend through a generated temp script file instead of an inline `/k` command string.
- CI re-verified green on GitHub Actions after pushing the fix commit (16da390): all 8 matrix jobs (windows-latest / ubuntu-latest x Python 3.10-3.13) passed, run 36219426861, https://github.com/SanHsien/agent-session-restore/actions/runs/36219426861.

## Files touched

pyproject.toml, src/agent_session_restore/cli.py, hooks.py, launcher.py, storage.py, models.py, src/agent_session_restore/quoting.py (added, 2026-09-26 follow-up), src/claude_session_restore/ (6 files deleted), hooks/session-start.py, hooks/session-end.py, scripts/Restore-AgentSessions.ps1, scripts/Restore-ClaudeSessions.ps1 (deleted), tests/test_launcher.py, tests/test_quoting.py (added, 2026-09-26 follow-up), tests/conftest.py, README.md, README.en.md (added, 2026-09-26 follow-up), docs/architecture.md (rewritten, 2026-09-26 follow-up), this REVIEW.md.
