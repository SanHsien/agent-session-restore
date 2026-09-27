# agent-session-restore (asr / csr)

[繁體中文](README.md) | English (this page)

> Windows-11-first session registry and fast-resume tool. Claude Code has real automatic hook tracking; Codex, Cursor, Antigravity, and Hermes are manual registration plus templated resume commands (see "Per-agent support level" below).

[![CI](https://github.com/SanHsien/agent-session-restore/actions/workflows/ci.yml/badge.svg)](https://github.com/SanHsien/agent-session-restore/actions)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Platform: Windows 11](https://img.shields.io/badge/Platform-Windows%2011%20Native-0078D6.svg)](https://microsoft.com)

---

## Problem & Concept

Heavy AI agent users routinely juggle multiple agents in parallel (Claude Code, OpenAI Codex, Cursor, Antigravity, Hermes, ...):
- Weekly reboots and updates: Windows 11 auto-update or a scheduled restart force-kills a dozen or more running agent sessions.
- Cross-agent context loss: after a reboot it is hard to remember what each session was doing, which agent it used, which directory it was in, and which git branch it was on.
- Manual resume is slow: you have to open a terminal, cd into each project directory, find the original session ID, and run the right resume command by hand, often 15 to 30 minutes and easy to miss one.

### Per-agent support level (honest breakdown)

Support is not uniform across agents. Check this table before relying on it for your workflow:

| Agent | Tracking | Notes |
| :--- | :--- | :--- |
| Claude Code | Automatic (SessionStart / SessionEnd hooks) | The only agent with real automatic integration; hooks write, update, and close registry entries when a session starts and ends. |
| Codex | Manual registration | You must run csr register --agent codex ... yourself; resume uses the template codex resume plus id. This repo does not auto-detect Codex sessions. |
| Cursor | Manual registration | Same as above; the resume template is cursor plus the working directory. |
| Antigravity | Manual registration | Same as above; the resume template is agy resume plus id. |
| Hermes | Manual registration | Same as above; the resume template is hermes resume plus id. |

In short: Claude Code works automatically once the hooks are installed. The other four agents need you (or that agent's own tooling) to call csr register yourself; this repo then handles building and launching the resume command.

### How it solves this
1. Semantic naming and agent typing: each session gets a clear task label and an agent tag (Claude / Codex / Cursor / Antigravity / Hermes / Custom).
2. Lifecycle hooks and registration (Claude Code only, automatic):
   - The hook writes session_id, agent, name, cwd, and git_branch automatically when a Claude Code session starts.
   - On an unexpected restart, a session that never got a clean SessionEnd stays marked active.
3. Fast restore engine: after a reboot, run one script or command and the tool relaunches every registered active session in Windows Terminal tabs or separate windows.

---

## Architecture

```
+------------------------------------------------------------------------------------+
|                                Multi-Agent Fleet Sessions                          |
|  [Claude Code] claude -n auth-fix     [Codex] codex resume task-42                 |
|  [Antigravity] agy resume flow-1      [Cursor] cursor /workspace                   |
+------------------------------------------------------------------------------------+
       | (SessionStart hook: Claude Code only)                | (SessionEnd hook: Claude Code only)
       v                                                      v
+-----------------------------+                        +-----------------------------+
|  Auto-register hook (Claude)|                        |    Session Cleanup/End      |
+-----------------------------+                        +-----------------------------+
       |                                                      |
       +------------------------------+-----------------------+
                                      |
                                      v
                       +-----------------------------+
                       |    SessionRegistry           |
                       | - Atomic File Locking        |
                       | - Multi-Agent Command Map    |
                       | - Auto-Branch Detection       |
                       | - Concurrency-Safe Writes     |
                       +-----------------------------+
                                      |
                                      v
                       +-----------------------------+
                       |   ~/.claude/                 |
                       |   claude-sessions.json       |
                       +-----------------------------+
                                      ^
                                      |
+------------------------------------------------------------------------------------+
|                        Restore Engine (Post-Reboot / On-Demand)                    |
|                                                                                    |
|   +------------------------------+        +------------------------------------+   |
|   |  Restore-AgentSessions.ps1    |   OR   |  csr restore -t wt [--agent ...]  |   |
|   +------------------------------+        +------------------------------------+   |
|                 |                                      |                           |
|                 +-------------------+------------------+                           |
|                                     v                                              |
|                    +----------------------------------+                            |
|                    |   Windows Terminal (wt.exe)      |                            |
|                    |   [CLAUDE] [CODEX] [AGY] [CURSOR]|                            |
|                    +----------------------------------+                            |
+------------------------------------------------------------------------------------+
```

---

## Features

- Fast batch restore: sessions launch with a small delay between each one (0.3s default) to smooth out CPU/disk I/O spikes.
- Deep Windows 11 integration: Windows Terminal (wt.exe) tabs with auto-set tab titles, plus standalone PowerShell (pwsh.exe) or CMD windows.
- Atomic, cross-process safe locking: Windows msvcrt file locking (fcntl on POSIX) plus atomic temp-file replacement (os.replace), so concurrent session starts never race or corrupt the JSON file.
- Smart metadata capture: Claude Code sessions auto-capture git branch, absolute working directory, and process ID.
- Two ways to use it: a Python CLI (csr/asr) and a native PowerShell script (Restore-AgentSessions.ps1).

---

## Getting Started

### 1. Get the project and set up the environment

This project uses the uv toolchain:

```powershell
cd agent-session-restore
$env:UV_LINK_MODE="copy"
uv sync --link-mode=copy
```

### 2. Configure Claude Code hooks (Claude Code only, not needed for the other agents)

Add the hooks to ~/.claude/settings.json:

Option A: run the install script (recommended)
```powershell
pwsh ./scripts/Install-Hooks.ps1
```
(the script backs up your existing settings.json before merging safely)

Option B: add the config manually to ~/.claude/settings.json:
```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "startup|clear|resume",
        "command": "python \"C:/path/to/agent-session-restore/hooks/session-start.py\""
      }
    ],
    "SessionEnd": [
      {
        "matcher": ".*",
        "command": "python \"C:/path/to/agent-session-restore/hooks/session-end.py\""
      }
    ]
  }
}
```

### 3. Manually register Codex / Cursor / Antigravity / Hermes sessions

These four agents have no automatic hook, so you call csr register yourself:
```powershell
uv run csr register --id codex-task-42 --agent codex --cwd C:\path\to\project --name "task-42"
uv run csr register --id cursor-session-1 --agent cursor --cwd C:\path\to\project
```

---

## Usage

### 1. After a dev machine reboot: restore every registered session in one step

Using the PowerShell script:
```powershell
pwsh ./scripts/Restore-AgentSessions.ps1
```
- Open in Windows Terminal tabs: ./scripts/Restore-AgentSessions.ps1 -Terminal wt
- Open in standalone windows: ./scripts/Restore-AgentSessions.ps1 -Terminal pwsh
- Preview only, no launch: ./scripts/Restore-AgentSessions.ps1 -DryRun

Or using the CLI (csr):
```powershell
uv run csr restore -t wt
uv run csr restore --dry-run
uv run csr restore --generate-script ./restore-fleet.ps1
```

### 2. List currently tracked sessions
```powershell
uv run csr list
uv run csr list --all
uv run csr list --json
```

### 3. Prune stale sessions
```powershell
uv run csr prune --days 14
uv run csr prune --missing-dirs
```

### 4. Export a session report
```powershell
uv run csr export --format md --output fleet-status.md
```

---

## Testing & Quality Gates

```powershell
uv run pytest -v
uv run ruff check .
uv run mypy src
```

---

## License

MIT License. Copyright (c) 2026 SanHsien.
