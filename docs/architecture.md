# agent-session-restore 架構與運作原理

本文件描述目前程式碼實際的多 Agent 架構，對應模組：`src/agent_session_restore/`（models.py、storage.py、registry.py、hooks.py、launcher.py、quoting.py、cli.py）、`hooks/session-start.py`、`hooks/session-end.py`、`scripts/Restore-AgentSessions.ps1`。

## 1. 追蹤方式：Claude Code 自動、其餘四種 Agent 手動

本專案對不同 Agent 的整合程度不同，這一點會影響下面每張圖怎麼讀：

- Claude Code：`hooks/session-start.py`／`hooks/session-end.py` 掛在 `~/.claude/settings.json` 的 `SessionStart`／`SessionEnd`，由 Claude Code 自動呼叫；不需要使用者手動介入。
- Codex／Cursor／Antigravity／Hermes：沒有對應的自動 Hook。使用者（或該 Agent 自己的工具鏈）必須自行執行 `csr register --agent <codex|cursor|antigravity|hermes> ...`（`src/agent_session_restore/cli.py` 的 `register` 子命令）才會被登錄進 registry。本專案不會偵測這些 Agent 何時啟動或結束。

## 2. 會話登錄檔格式 (Registry Format)

登錄檔預設路徑：`~/.claude/claude-sessions.json`（`storage.py` 的 `get_default_registry_path()`；可用環境變數 `CLAUDE_SESSION_REGISTRY_FILE` 或 CLI 的 `--file` 覆寫）。

結構對應 `models.py` 的 `SessionRegistryData` 與 `SessionEntry`：

```
{
  "version": 1,
  "updated_at": "<ISO 8601 UTC>",
  "sessions": {
    "<session_id>": {
      "session_id":       str,   # 嚴格格式驗證，見第 6 節
      "name":              str,   # 顯示用文字，只過濾控制字元
      "cwd":               str,   # Path.resolve() 後的絕對路徑，過濾非法路徑字元
      "agent":             str,   # claude / codex / cursor / antigravity / hermes / custom
      "status":            str,   # active / closed / archived
      "created_at":        str,
      "updated_at":        str,
      "git_branch":        str | null,
      "pid":               int | null,
      "notes":             str | null,
      "custom_resume_cmd": str | null   # 任意、視為受信任的指令樣板，見第 6 節
    }
  }
}
```

## 3. Claude Code Hook 流程（唯一自動路徑）

```
Claude Code 本體
    |
    | SessionStart 事件 (settings.json 設定觸發)
    v
hooks/session-start.py
    |  將 src/ 加進 sys.path，import agent_session_restore.hooks
    v
agent_session_restore.hooks.handle_session_start()
    |  parse_hook_payload(): 讀 stdin JSON，失敗則退回環境變數
    |  (CLAUDE_SESSION_ID / CLAUDE_SESSION_NAME / CLAUDE_PROJECT_DIR)
    v
agent_session_restore.registry.SessionRegistry.register(agent="claude", ...)
    |  detect_git_branch(): 呼叫 git rev-parse 取得分支
    v
agent_session_restore.storage.SessionStorage.save()
    |  取得檔案鎖 -> 寫入 .tmp 檔 -> os.replace() 原子替換
    v
~/.claude/claude-sessions.json （status = active）

--- 會話結束時 ---

Claude Code 本體 --SessionEnd 事件--> hooks/session-end.py
    --> handle_session_end() --> registry.unregister(hard_delete=False)
    --> 該筆 entry.status 改為 closed（預設不刪除，供歷史查詢／匯出）

--- 意外中斷（當機、Windows 更新強制重開機）---
沒有 SessionEnd 事件 -> entry 永遠停留在 status = active
-> 這正是 restore 引擎判斷「該恢復哪些會話」的依據（見第 4 節）。
```

`hooks/session-start.py` 與 `hooks/session-end.py` 皆以 `sys.exit(0)` 結尾（`try/except Exception` 包住整個流程，錯誤只寫進 `~/.claude/session-restore-hook.log`），確保 Hook 本身絕不阻擋 Claude Code 開場或關閉。

## 4. Codex／Cursor／Antigravity／Hermes：手動登錄路徑

```
使用者（或該 Agent 自己的腳本／工具鏈）
    |
    | 手動執行： uv run csr register --agent codex --id <id> --cwd <dir> ...
    v
agent_session_restore.cli.cmd_register()
    v
SessionRegistry.register(agent="codex", ...)   # 與 Hook 路徑共用同一個 API
    v
SessionStorage.save() --> ~/.claude/claude-sessions.json
```

這條路徑與第 3 節共用 `SessionRegistry`／`SessionStorage`，差別只在於「誰、何時呼叫 register()」：Claude Code 由 Hook 自動呼叫；其餘四種 Agent 需要人工或外部腳本呼叫，本專案本身不監看它們的行程或視窗。

## 5. 恢復／啟動流程 (Restore / Launch)

```
使用者：uv run csr restore -t wt        或    pwsh ./scripts/Restore-AgentSessions.ps1
    |
    v
讀取 registry -> 篩選 status = active（可用 --agent 依 Agent 篩選）
    |
    v
針對每個 session：
  agent_session_restore.launcher.SessionLauncher.build_agent_argv(session)
      -> 依 session.agent 決定內建範本 argv：
           codex        -> ["codex", "resume", <id>]
           cursor       -> ["cursor", <cwd>]
           antigravity  -> ["agy", "resume", <id>]
           hermes       -> ["hermes", "resume", <id>]
           claude/其他  -> ["claude", "-r", <id>]
      -> 若 session 帶有 custom_resume_cmd（任意、受信任的樣板字串），
         改用 {id}/{session_id}/{cwd}/{name} 佔位符替換，不走上面的 argv 表。
    |
    v
agent_session_restore.launcher.SessionLauncher.build_resume_command(session)
      -> 依終端後端（wt / pwsh / powershell / cmd）組出最終要執行的 argv list：
         - wt / pwsh / powershell 後端：用 quoting.format_argv_powershell()
           或 quoting.quote_powershell() 把上面的 argv／cwd／title 轉成
           單引號 PowerShell 字面值，交給 `pwsh -Command <script>`。
         - cmd 後端：用 quoting.format_argv_cmd() 或 quoting.quote_cmd_token()
           把同樣的值轉成 cmd.exe 安全格式，交給 `cmd /k <command>`。
    |
    v
subprocess.Popen(argv, shell=False)  # 一律不經過額外 shell 轉譯
      -> wt.exe 開新分頁 / pwsh 或 powershell 獨立視窗 / cmd 獨立視窗
```

`scripts/Restore-AgentSessions.ps1` 是同一套邏輯的 PowerShell 版本（不需要 Python/uv 環境即可執行），內含對應的 `ConvertTo-PSLiteral` 與 `ConvertTo-CmdSafe` 兩個函式，行為對齊 `quoting.py` 的 `quote_powershell()`／`quote_cmd_token()`。

## 6. 輸入驗證與命令組裝安全性

`models.py` 的 `SessionEntry.__post_init__` 在寫入 registry 前驗證：

- `session_id`：必須符合 `^[A-Za-z0-9_.:-]{1,128}$`，不符合直接丟出 `ValueError`（拒絕而非轉義，因為 session id 沒有合理理由包含空白或 shell 特殊字元）。
- `cwd`：`Path(cwd).resolve()` 正規化後，檢查不含 `< > " | ? *` 與 ASCII 控制字元；刻意不要求目錄「必須存在」，因為 `csr prune --missing-dirs` 與 launcher 本身的「目錄不存在」判斷都需要能先讀到已登錄但目錄已被刪除的 entry。
- `name`：只過濾 ASCII 控制字元，其餘字元一律保留 —— 因為 name 是自由格式的顯示文字，真正的安全邊界在 `launcher.py`／`Restore-AgentSessions.ps1` 組出命令字串時一定會用對應 shell 的引號函式包起來，而不是在輸入端強制拒絕標點符號。
- `custom_resume_cmd`：刻意維持任意字串（視為受信任輸入，通常由使用者自己在自己的機器上填寫），只有被替換進去的 `{id}`／`{cwd}`／`{name}` 片段會經過引號處理，樣板本身的語法不受任何限制。

## 7. 併發安全的檔案鎖定 (FileLock)

`storage.py` 的 `FileLock`：

```
acquire()
  Windows (sys.platform == "win32"): msvcrt.locking(fd, LK_NBLCK, 1)
  POSIX                            : fcntl.flock(fd, LOCK_EX | LOCK_NB)
  取不到鎖 -> 每 50ms 重試，最長 timeout_seconds（預設 5 秒）後放棄繼續執行
             （避免 Hook 因鎖死而卡住 Claude Code 啟動流程）

save()
  寫進 .{filename}.tmp-{pid}-{uuid} 暫存檔 -> os.replace() 原子替換正式檔
  （即使十幾個 SessionStart 同時觸發，也不會出現半寫入的殘缺 JSON）
```

`[tool.mypy]` 設定 `platform = "win32"`：因為 `msvcrt`／`fcntl` 兩個模組的 typeshed 型別樁是依平台切換的，固定成 win32 讓 CI 在 Windows 與 Linux runner 上得到一致的型別檢查結果（詳見專案根目錄 `REVIEW.md`）。
