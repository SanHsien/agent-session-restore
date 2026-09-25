# CLAUDE.md

## 專案概要
`agent-session-restore` (asr)：Windows 11 原生 Multi-Agent（Claude Code, Codex, Cursor, Antigravity, Hermes）艦隊級會話註冊與 30 秒快速恢復系統。

## 日常工作流
- 安裝／同步依賴：`uv sync --link-mode=copy`
- 執行測試：`uv run pytest -v`
- Lint：`uv run ruff check .`
- 型別檢查：`uv run mypy src`
- CLI 測試：`uv run asr list` 或 `uv run asr restore --dry-run`

## 提交前檢驗門檻
回報完成前必須執行：
```powershell
uv run ruff check .
uv run mypy src
uv run pytest -v
```
並確認回傳代碼為 0 且全部 30+ 測試項目綠燈。
