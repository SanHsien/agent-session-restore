# 路線圖

> 本 repo 後續工作的單一真相源。做完一項，就在同一個 commit 刪掉那一列；新工作只加在這裡。
> 現況段的數字是 2026-10-29 依 `main` `22497ed79ffb` 查的快照，開工前先重查 GitHub。

## 現況

public、非 fork 的 Windows 優先多 Agent 會話登記與恢復工具（`csr`）：Claude Code 以 hook 自動登記，Codex、Cursor、Antigravity、Hermes 手動登記。健康：2026-10-29 查到 open issue 0、open PR 0、Dependabot 與 Code scanning 告警各 0，`CI`、`CodeQL`、`Dependency freshness` 在 `main` 最近一次皆 success（2026-10-29 `gh` 查詢）；沒有 release。10-26 的深度掃描修復已全部在 `main`，PowerShell 腳本的 cmd 模式也已改用暫存批次檔；剩下的是需要實機 Windows Terminal／互動 cmd 視窗才能驗證的邊角，以及中期條件項目。

## 近期：可以直接動手
| 項目 | 為什麼 | 驗收方式 | 建議執行者 | 證據 |
| --- | --- | --- | --- | --- |
| 無 | 目前沒有可以直接動手的項目 | 無 | 無 | 無 |

## 中期：等條件成立再做
| 項目 | 觸發條件 | 驗收方式 | 建議執行者 | 證據 |
| --- | --- | --- | --- | --- |
| 讓 `custom_resume_cmd` 樣板文字也套用 level-1 跳脫 | 維護者決定樣板語意要等同「直接打在內層 cmd」（行為變更）；注意 cmd 後端現在把樣板寫進暫存腳本的括號區塊，語意已是「批次行」（`%` 要寫 `%%`、未引號 `)` 會破壞區塊），決定時以此為基準 | 樣板中的 `&&` 等語法依決定的語意生效或被跳脫，並寫進 `README.md` 與 `docs/architecture.md:138`；新增釘住該語意的測試 | Claude Opus | 深度掃描報告（2026-10-26）「範圍外、未改」；`docs/architecture.md:138` |
| 為 Codex、Cursor、Antigravity、Hermes 做自動偵測 | 維護者要求，且取得各 agent 本機 session 儲存格式的契約 | 該 agent 不需 `csr register` 即可被登記；README 的「Per-agent support level」表同步更新；測試用假資料，不碰真實 session | Claude Sonnet | 接續開發報告「刻意不做」；`README.en.md` 的 Per-agent support level |
| 發布第一個 release | 維護者決定要對外交付或固定可回溯版本 | `gh release list --repo SanHsien/agent-session-restore` 有 tag；`CHANGELOG` 或 release notes 對應 `git log` | 維護者 | 2026-10-29 `gh release list`：無 release |

## 刻意不做
| 不做什麼 | 理由 | 什麼情況下重新考慮 |
| --- | --- | --- |
| 讓 `from_dict` 對驗證失敗的舊項目報錯而非靜默丟棄 | 設計取捨；產生壞項目的路徑已堵住（寫入前驗證） | 出現使用者資料被靜默丟失的實例 |
| 讓 `prune` 處理無時區的 `updated_at` | 程式自己只寫帶時區的值，只有手改檔才會出現 | 新增匯入外部 registry 的功能 |
| 為了展示而新增依賴 | 執行期 `dependencies` 為空；`ruff` 下限升到 0.17.2 是真實開發工具升版 | 功能需要第三方套件，且有明確理由 |

## 維護方式

自有 repo，在 `main` 上直推，無 release 節奏。改動前跑 `uv run pytest -q -W error`、`uv run ruff check .`、`uv run mypy src`（CI 跑 `uv run ruff check .`、`uv run mypy src`、`uv run pytest -v`；本機多加 `-q -W error` 較嚴）；Dependabot 每週追蹤 `uv` 與 GitHub Actions，`Dependency freshness` workflow 不靠單純拉高下限消紅燈。
