# AGENTS.md — 代理開發與維護規範

本專案遵守 SanHsien 專案之全域治理與 Windows 11 原生開發標準。

## 開發指令
- 套件環境：`uv sync --link-mode=copy`
- 執行測試：`uv run pytest -v`
- 程式檢查：`uv run ruff check .`
- 型別檢查：`uv run mypy src`
- 完整校驗：`uv run ruff check . ; uv run mypy src ; uv run pytest -v`

## 核心設計原則
1. **Windows 11 原生優先**：路徑一律以 Windows 原生路徑與 `Path.resolve()` 處理，終端機調用支援 Windows Terminal (`wt.exe`) 與 PowerShell (`pwsh.exe`)。
2. **Hook 永不斷線**：Hook 腳本永遠以 `sys.exit(0)` 結尾，不論讀取失敗、逾時或格式錯誤，絕不阻礙 Claude Code 開場進入。
3. **零殘缺寫入**：狀態存儲必經排他鎖與臨時檔案原子替換。
4. **驗證先行**：宣稱任何修改完成前，必須實際執行 pytest 與靜態分析並檢視輸出。
