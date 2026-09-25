# agent-session-restore (asr / csr) 🚀

> **Windows 11 原生優先的 Multi-Agent（Claude Code, Codex, Cursor, Antigravity, Hermes）艦隊級會話註冊與 30 秒閃電恢復工具**  
> *Windows-first fast resume & session registry manager for AI Agent fleets (Claude, Codex, Cursor, Antigravity, Hermes).*

[![CI](https://github.com/SanHsien/agent-session-restore/actions/workflows/ci.yml/badge.svg)](https://github.com/SanHsien/agent-session-restore/actions)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Platform: Windows 11](https://img.shields.io/badge/Platform-Windows%2011%20Native-0078D6.svg)](https://microsoft.com)

---

## 📖 痛點分析與核心理念 (Problem & Concept)

現代重度 AI Agent 開發者常在多個 Agent 之間平行切換（如 **Claude Code**, **OpenAI Codex**, **Cursor**, **Antigravity (AGY)**, **Hermes** 等）：
- **每週系統重啟與更新**：Windows 11 自動更新或定時重啟後，正在運作中的 10～20 個 Agent 會話被強制中止。
- **跨 Agent 脈絡記憶斷層 (Context Loss)**：重啟後極難記住每個會話各自在處理什麼任務、使用哪種 Agent、原本在哪個目錄、對應哪條 git branch。
- **手動恢復痛苦低效**：必須手動開啟終端機、逐一 `cd` 到各專案目錄、找出原本的 session ID 執行 `claude -r <id>`、`codex resume <id>`、`agy resume <id>`、`cursor .` 等，往往耗費 15～30 分鐘且容易遺漏。

### 本專案的解決架構
1. **語義命名與 Agent 分類 (Naming & Typing)**：賦予會話明確任務標籤，並標註 Agent 種類（Claude / Codex / Cursor / Antigravity / Hermes / Custom）。
2. **生命週期鉤子與註冊 (Hooks & Registry)**：
   - 鉤子在會話啟動時自動寫入 `session_id`、`agent`、`name`、`cwd` 與 `git_branch`。
   - 意外重啟時，未完成的會話自動保持在 `active` 狀態！
3. **30 秒閃電跨 Agent 恢復引擎 (Fast Multi-Agent Restorer)**：
   重開機後，只需執行單一腳本或指令，系統以 Windows Terminal (`wt.exe`) 分頁或獨立視窗，在 30 秒內自動將所有不同 Agent 的活躍會話全數還原！

---

## 🏗️ 運作架構圖 (Architecture)

```
+------------------------------------------------------------------------------------+
|                                Multi-Agent Fleet Sessions                          |
|  [Claude Code] claude -n auth-fix     [Codex] codex resume task-42                 |
|  [Antigravity] agy resume flow-1      [Cursor] cursor /workspace                   |
+------------------------------------------------------------------------------------+
       | (SessionStart hooks / CLI register)                  | (SessionEnd hook)
       v                                                      v
+-----------------------------+                        +-----------------------------+
|    Hooks & Auto-Registrar   |                        |    Session Cleanup/End      |
+-----------------------------+                        +-----------------------------+
       |                                                      |
       +------------------------------+-----------------------+
                                      |
                                      v
                       +-----------------------------+
                       |    SessionRegistryManager   |
                       | - Atomic File Locking       |
                       | - Multi-Agent Command Map   |
                       | - Auto-Branch Detection     |
                       | - Concurrency-Safe Writes   |
                       +-----------------------------+
                                      |
                                      v
                       +-----------------------------+
                       |   ~/.claude/                |
                       |   claude-sessions.json      |
                       +-----------------------------+
                                      ^
                                      |
+------------------------------------------------------------------------------------+
|                        Restore Engine (Post-Reboot / On-Demand)                    |
|                                                                                    |
|   +------------------------------+        +------------------------------------+   |
|   |  Restore-AgentSessions.ps1   |   OR   |  csr restore -t wt [--agent ...]   |   |
|   +------------------------------+        +------------------------------------+   |
|                 |                                      |                           |
|                 +-------------------+------------------+                           |
|                                     v                                              |
|                    +----------------------------------+                            |
|                    |   Windows Terminal (wt.exe)      |                            |
|                    |   [CLAUDE] [CODEX] [AGY] [CURSOR]|                            |
|                    +----------------------------------+                            |
|                                     |                                              |
|                                     v                                              |
|                    All 19+ sessions restored in ~30s!                              |
+------------------------------------------------------------------------------------+
```

---

---

## ✨ 核心特色 (Features)

- **⚡ 30 秒批次恢復**：智慧排程每個會話啟動間隔（預設 0.3 秒），平滑避免 CPU 與磁碟 I/O 尖峰，19 個會話約 30 秒完成喚醒。
- **🪟 Windows 11 原生深度適配**：
  - 支援 **Windows Terminal (`wt.exe`) 分頁標籤**，將所有會話整齊收納在單一視窗的不同 Tabs，並自動設定 Tab Title 為會話名稱。
  - 支援 **PowerShell (`pwsh.exe`)** 獨立視窗與 **CMD** 模式。
- **🛡️ 跨進程原子安全鎖定**：採用 Windows `msvcrt` 檔案鎖定與臨時檔原子替換（`os.replace`），即使十幾個會話同時 SessionStart 也不會發生 Race Condition 或 JSON 毀損。
- **🌿 智慧元資料解析**：會話啟動時自動萃取 Git Branch、工作目錄絕對路徑與 Process ID。
- **🧰 雙模體驗**：提供 Python CLI (`csr`) 與原生 PowerShell 腳本 (`Restore-ClaudeSessions.ps1`)。

---

## 🚀 快速安裝與設定 (Getting Started)

### 1. 取得專案與建置環境
本專案採用現代化 `uv` 工具鏈：

```powershell
# 進入專案目錄
cd claude-session-restore

# 使用 uv 同步環境
$env:UV_LINK_MODE="copy"
uv sync --link-mode=copy
```

### 2. 設定 Claude Code Hooks
將 Hooks 掛載至 `~/.claude/settings.json`：

#### 方式 A：執行自動安裝腳本（推薦）
```powershell
pwsh ./scripts/Install-Hooks.ps1
```
*(腳本會先自動備份原有的 `settings.json` 再進行安全合併)*

#### 方式 B：手動添加設定
在 `~/.claude/settings.json` 中加入：
```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "startup|clear|resume",
        "command": "python \"C:/path/to/claude-session-restore/hooks/session-start.py\""
      }
    ],
    "SessionEnd": [
      {
        "matcher": ".*",
        "command": "python \"C:/path/to/claude-session-restore/hooks/session-end.py\""
      }
    ]
  }
}
```

---

## 💻 使用方法 (Usage)

### 1. 開發機重啟後：一鍵恢復所有會話

#### 使用 PowerShell 腳本：
```powershell
pwsh ./scripts/Restore-ClaudeSessions.ps1
```
- 以 Windows Terminal 分頁開啟：`./scripts/Restore-ClaudeSessions.ps1 -Terminal wt`
- 以獨立視窗開啟：`./scripts/Restore-ClaudeSessions.ps1 -Terminal pwsh`
- 僅預覽不啟動：`./scripts/Restore-ClaudeSessions.ps1 -DryRun`

#### 或使用 CLI 工具 (`csr`)：
```powershell
# 恢復活躍會話至 Windows Terminal 分頁
uv run csr restore -t wt

# 預覽恢復命令
uv run csr restore --dry-run

# 生成獨立的 PowerShell 恢復腳本
uv run csr restore --generate-script ./restore-fleet.ps1
```

### 2. 檢視目前追蹤的會話清單
```powershell
# 列出當前活躍會話
uv run csr list

# 列出包含已關閉的所有歷史會話
uv run csr list --all

# 以 JSON 格式輸出
uv run csr list --json
```

### 3. 清理過期會話
```powershell
# 清理 14 天以前已關閉的過期會話
uv run csr prune --days 14

# 清理目錄已不存在的失效會話
uv run csr prune --missing-dirs
```

### 4. 導出會話清單報告
```powershell
# 導出為 Markdown 表格
uv run csr export --format md --output fleet-status.md
```

---

## 🧪 測試與品質檢驗 (Testing & Quality Gates)

本專案遵守嚴格的工程驗證門檻：

```powershell
# 執行所有測試（30+ 測試項目，覆蓋並發鎖、CLI、Hook、Windows Terminal）
uv run pytest -v

# 執行 Ruff Lint 檢查
uv run ruff check .

# 執行 Mypy 嚴格型別檢查
uv run mypy src
```

---

## 📄 授權條款 (License)

MIT License. Copyright (c) 2026 SanHsien.
