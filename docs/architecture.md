# claude-session-restore 架構與運作原理

## 1. 系統架構圖 (Architecture Overview)

```
+------------------------------------------------------------------------------------+
|                                Claude Code Sessions                                |
|  [Session A] claude -n auth-fix      [Session B] claude -n ci-perf      ... (19+)   |
+------------------------------------------------------------------------------------+
       | (SessionStart hook)                                  | (SessionEnd hook)
       v                                                      v
+-----------------------------+                        +-----------------------------+
|    hooks/session-start.py   |                        |    hooks/session-end.py     |
+-----------------------------+                        +-----------------------------+
       |                                                      |
       +------------------------------+-----------------------+
                                      |
                                      v
                       +-----------------------------+
                       |    SessionRegistryManager   |
                       | - Atomic File Locking       |
                       | - Auto-Branch Detection     |
                       | - Session Metadata Normalizer|
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
|   |  Restore-ClaudeSessions.ps1  |   OR   |  csr restore -t wt                 |   |
|   +------------------------------+        +------------------------------------+   |
|                 |                                      |                           |
|                 +-------------------+------------------+                           |
|                                     v                                              |
|                    +----------------------------------+                            |
|                    |   Windows Terminal (wt.exe)      |                            |
|                    |   Tabs: [auth-fix] [ci-perf] ... |                            |
|                    |   Command: claude -r <id>        |                            |
|                    +----------------------------------+                            |
|                                     |                                              |
|                                     v                                              |
|                    All 19 sessions restored in ~30s!                               |
+------------------------------------------------------------------------------------+
```

## 2. 生命週期與事件時序 (Lifecycle Sequence)

```
Claude Code               Hook Script              SessionStorage              Registry File
    |                          |                         |                           |
    |--- SessionStart -------->|                         |                           |
    |    (id, cwd, name)       |--- Acquire Lock ------->|                           |
    |                          |--- Atomic Save -------->|--- Write (.tmp) --------->|
    |                          |                         |--- os.replace ----------->| [Active]
    |                          |<-- Success -------------|                           |
    |<-- {"systemMessage"} ----|                         |                           |
    |                          |                         |                           |
    | ... working ...          |                         |                           |
    |                          |                         |                           |
 [OS Reboot / Crash]           |                         |                           |
    x (No SessionEnd triggered)|                         |                           |
    |                          |                         |                           | [Remains Active]
    |                          |                         |                           |
 [Post-Reboot Restore]         |                         |                           |
    |                          |                         |                           |
 User / Task Scheduler         |                         |                           |
    |--- Restore Script ------>|                         |                           |
    |                          |--- Read Active Entries->|--- Parse JSON ----------->|
    |<-- Launch wt tabs -------|                         |                           |
    |    (30s for 19 sessions) |                         |                           |
```

## 3. 關鍵設計決策

1. **非同步安全與檔案鎖定 (FileLock)**：
   多個會話在同秒啟動時，若直接寫入同一 JSON 檔極易發生 `Race Condition` 或產生半寫入之殘缺 JSON。我們採用 Windows `msvcrt.locking`（相容 Linux `fcntl.flock`）並以 `.tmp.<pid>.<uuid>` 進行 `os.replace` 原子替換。
2. **意外重啟的保護力**：
   正常的 SessionEnd 會將 status 標記為 `closed`；而每週 Windows Update 或意外重啟會直接中斷進程，這些會話將保持在 `active` 狀態，開機恢復腳本即可精準抓出所有「重啟前未完工」的會話。
3. **Windows 11 原生分頁 (Windows Terminal wt.exe)**：
   使用 Windows 11 原生 `wt.exe -w 0 new-tab -d "<cwd>" --title "<name>"`，避免螢幕彈出 19 個混亂視窗，統一整齊地收納在單一終端機分頁列。
