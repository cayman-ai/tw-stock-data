# AGENTS.md — tw-stock-data

給 Codex 與其他 agent 的工作規則。完整需求見 `docs/SPEC.md`。

## 一定要遵守

- 所有說明、報告、註解用繁體中文（台灣用語）。
- 只用官方公開資料（公開資訊觀測站、證交所、櫃買中心）。查不到就留 `null` 並記進 reports/coverage.md，不可估算或補 0。
- 抓取要禮貌：請求間隔 ≥ 1.5 秒、失敗退避重試、可中斷續跑。不要並行轟炸官方網站。
- `data/raw/` 不上傳（寫進 .gitignore）。
- **不要 git commit、不要 push**。做完把結果留在本機資料夾，由 Claude 驗收後上傳。
- 不要修改 `legacy/` 與 `reference/`，那是參考用的舊程式。
- Python 3.11，套件以標準函式庫、requests、pandas 為限；用到的套件寫進 requirements.txt。

## 遇到以下情況就停下來回報，不要自己猜

- 規格有兩種以上解讀。
- 官方網站封鎖、改版，或抓取方式需要繞過驗證。
- 同一個錯誤修兩次仍失敗。
- 需要刪除或覆寫已抓好的資料。

## 回報格式

做完（或停下來）時，在 `reports/codex-report.md` 寫：改了哪些檔、各完成標準的結果、未解決的問題、建議下一步。
