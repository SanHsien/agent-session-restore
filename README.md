# agent-session-restore (asr / csr)

[English](README.en.md) | 繁體中文（本頁）

> Windows 11 原生優先的會話註冊與 30 秒閃電恢復工具。Claude Code 具備自動 Hook 追蹤；Codex、Cursor、Antigravity、Hermes 為手動註冊 + 範本式恢復指令（詳見下方「各 Agent 支援程度」）。

[![CI](https://github.com/SanHsien/agent-session-restore/actions/workflows/ci.yml/badge.svg)](https://github.com/SanHsien/agent-session-restore/actions)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Platform: Windows 11](https://img.shields.io/badge/Platform-Windows%2011%20Native-0078D6.svg)](https://microsoft.com)

---

## 痛點分析與核心理念 (Problem & Concept)

重度 AI Agent 開發者常在多個 Agent 之間平行切換（如 Claude Code、OpenAI Codex、Cursor、Antigravity、Hermes 等）：
- 每週系統重啟與更新：Windows 11 自動更新或定時重啟後，正在運作中的多個 Agent 會話被強制中止。
- 跨 Agent 脈絡記憶斷層：重啟後很難記住每個會話各自在處理什麼任務、使用哪種 Agent、原本在哪個目錄、對應哪條 git branch。
- 手動恢復耗時：必須手動開啟終端機、逐一 cd 到各專案目錄、找出原本的 session ID 再執行對應指令，往往耗費 15 到 30 分鐘且容易遺漏。

### 各 Agent 支援程度（誠實說明）

本專案對不同 Agent 的支援程度並不一致，請依此決定是否適合你的工作流：

| Agent | 追蹤方式 | 說明 |
| :--- | :--- | :--- |
| Claude Code | 自動（SessionStart / SessionEnd Hook） | 唯一有真正自動整合的 Agent；Hook 會在會話啟動與結束時自動寫入/更新/關閉登錄檔項目。 |
| Codex | 手動註冊 | 需自行執行 csr register --agent codex ...；恢復時使用範本指令 codex resume 加上 id，本專案不會自動偵測 Codex 會話。 |
| Cursor | 手動註冊 | 同上，範本指令為 cursor 加上工作目錄。 |
| Antigravity | 手動註冊 | 同上，範本指令為 agy resume 加上 id。 |
| Hermes | 手動註冊 | 同上，範本指令為 hermes resume 加上 id。 |

簡言之：Claude Code 是掛上就自動運作，其餘四種 Agent 是你（或該 Agent 自己的工具鏈）要自己呼叫 csr register，本專案再幫你把恢復指令排好。

### 本專案的解決架構
1. 語義命名與 Agent 分類：賦予會話明確任務標籤，並標註 Agent 種類（Claude / Codex / Cursor / Antigravity / Hermes / Custom）。
2. 生命週期 Hook 與註冊（僅 Claude Code 自動）：
   - Hook 在 Claude Code 會話啟動時自動寫入 session_id、agent、name、cwd 與 git_branch。
   - 意外重啟時，未正常結束的會話自動保持在 active 狀態。
3. 30 秒閃電恢復引擎：重開機後執行單一腳本或指令，系統以 Windows Terminal (wt.exe) 分頁或獨立視窗，快速將已登錄的活躍會話全數還原。

---

## 運作架構圖 (Architecture)

```
+------------------------------------------------------------------------------------+
|                                Multi-Agent Fleet Sessions                          |
|  [Claude Code] claude -n auth-fix     [Codex] codex resume task-42                 |
|  [Antigravity] agy resume flow-1      [Cursor] cursor /workspace                   |
+------------------------------------------------------------------------------------+
       | (SessionStart hook: Claude Code only)                | (SessionEnd hook: Claude Code only)
       v                                                      v
+-----------------------------+                        +-----------------------------+
|  Hook 自動註冊（僅 Claude） |                        |    Session Cleanup/End      |
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

## 核心特色 (Features)

- 批次快速恢復：智慧排程每個會話啟動間隔（預設 0.3 秒），平滑避免 CPU 與磁碟 I/O 尖峰。
- Windows 11 原生深度適配：支援 Windows Terminal (wt.exe) 分頁標籤，並自動設定 Tab Title 為會話名稱；也支援 PowerShell (pwsh.exe) 獨立視窗與 CMD 模式。
- 跨進程原子安全鎖定：採用 Windows msvcrt 檔案鎖定（POSIX 上為 fcntl）與臨時檔原子替換（os.replace），避免多個會話同時寫入造成 Race Condition 或 JSON 毀損。
- 智慧元資料解析：Claude Code 會話啟動時自動萃取 Git Branch、工作目錄絕對路徑與 Process ID。
- 雙模體驗：提供 Python CLI (csr/asr) 與原生 PowerShell 腳本 (Restore-AgentSessions.ps1)。

---

## 快速安裝與設定 (Getting Started)

### 1. 取得專案與建置環境

本專案採用 uv 工具鏈：

```powershell
cd agent-session-restore
$env:UV_LINK_MODE="copy"
uv sync --link-mode=copy
```

### 2. 設定 Claude Code Hooks（僅 Claude Code 需要，其他 Agent 不適用）

將 Hooks 掛載至 ~/.claude/settings.json：

方式 A：執行自動安裝腳本（推薦）
```powershell
pwsh ./scripts/Install-Hooks.ps1
```
（腳本會先自動備份原有的 settings.json 再進行安全合併）

方式 B：手動添加設定，在 ~/.claude/settings.json 中加入：
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

### 3. 為 Codex / Cursor / Antigravity / Hermes 手動註冊會話

這四種 Agent 沒有自動 Hook，需要自行呼叫 csr register：
```powershell
uv run csr register --id codex-task-42 --agent codex --cwd C:\path\to\project --name "task-42"
uv run csr register --id cursor-session-1 --agent cursor --cwd C:\path\to\project
```

---

## 使用方法 (Usage)

### 1. 開發機重啟後：一鍵恢復所有已登錄會話

使用 PowerShell 腳本：
```powershell
pwsh ./scripts/Restore-AgentSessions.ps1
```
- 以 Windows Terminal 分頁開啟：./scripts/Restore-AgentSessions.ps1 -Terminal wt
- 以獨立視窗開啟：./scripts/Restore-AgentSessions.ps1 -Terminal pwsh
- 僅預覽不啟動：./scripts/Restore-AgentSessions.ps1 -DryRun

或使用 CLI 工具 (csr)：
```powershell
uv run csr restore -t wt
uv run csr restore --dry-run
uv run csr restore --generate-script ./restore-fleet.ps1
```

### 2. 檢視目前追蹤的會話清單
```powershell
uv run csr list
uv run csr list --all
uv run csr list --json
```

### 3. 清理過期會話
```powershell
uv run csr prune --days 14
uv run csr prune --missing-dirs
```

### 4. 導出會話清單報告
```powershell
uv run csr export --format md --output fleet-status.md
```

---

## 測試與品質檢驗 (Testing & Quality Gates)

```powershell
uv run pytest -v
uv run ruff check .
uv run mypy src
```

---

## 授權條款 (License)

MIT License. Copyright (c) 2026 SanHsien.
