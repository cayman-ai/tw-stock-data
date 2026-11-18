# tw-stock-data 規格書（v1.1，2026-11-18 依兩輪審查修訂）

台股上市＋上櫃全市場基本面資料庫，供「冰火島・股票島」的 EPS 速查與選股篩選頁使用。個人使用，資料全部來自官方公開來源。

## 1. 範圍

- 公司：上市（TWSE）＋上櫃（TPEx）普通股。不含興櫃、ETF、特別股、存託憑證。
- 歷史長度：季報 20 季、月營收 60 個月、股利 5 個盈餘年度、股價與本益比只要最新一日（歷史本益比 36 個月為加分項，非必要）。
- 金額單位一律存「千元」，與官方彙總表一致；EPS、股利存「元」。

## 2. 資料來源（優先順序）

1. **全市場彙總表**（一次一個期間、全部公司），務必優先使用：
   - 月營收：公開資訊觀測站每月營收彙總表（上市、上櫃各一張／月）。
   - 季報：公開資訊觀測站「綜合損益表／資產負債表」彙總報表（依產業格式分表，一季數張）。
   - 現金流量（營業現金流、資本支出）：若彙總表沒有，才逐家抓 XBRL（參考 `legacy/scripts/ingest_xbrl.py`）。
   - 股利：證交所／櫃買中心 OpenAPI（見 `legacy/src/official-sources.mjs`）與公開資訊觀測站股利分派彙總。
   - 股價、本益比、股價淨值比、殖利率：證交所 BWIBBU_ALL／STOCK_DAY_ALL、櫃買中心對應 OpenAPI。
2. 逐家抓取只用在彙總表缺的欄位。
3. 實際網址與欄位以實測為準；`legacy/` 是舊專案 CT-EPS-Analytics 的程式，已驗證可用的抓法請沿用。

**禮貌抓取**：每次請求間隔 ≥ 1.5 秒、失敗退避重試 3 次、帶 User-Agent、可中斷續跑（已抓過的期間不重抓）。原始回應存 `data/raw/`（.gitignore，不上傳）。

## 3. 輸出檔（全部上傳 GitHub，除了 data/raw）

```
data/history/companies.csv        code,name,market,industry,listed_shares
data/history/monthly_revenue.csv  code,year,month,revenue
data/history/quarterly.csv        code,year,quarter,revenue,gross_profit,operating_expense,operating_income,
                                  non_operating,pretax_income,income_tax,net_income,parent_net_income,
                                  eps,parent_equity,total_liabilities,total_assets,operating_cash_flow,capex
                                  （全部為「單季」數值；累計數要自行轉單季）
data/history/dividends.csv        code,earnings_year,cash_dividend,stock_dividend
data/latest/prices.csv            code,date,close,pe,pb,dividend_yield
data/site/stocks.json             網頁讀取用成品，見第 5 節
reports/coverage.md               各資料集覆蓋率、缺漏清單
reports/validation.md             抽樣核對結果
reports/backtest.md               全市場回測結果
```

## 4. 計算方法（已由 12 檔回測選定，見 reference/backtest/）

**今年預估 EPS**（主算法「旺淡季營收＋近四季淨利率」）
- 已公布季度：直接加總實際 EPS。
- 全年營收 ＝ 今年已公布月份累計 × 旺淡季係數。旺淡季係數 ＝ 前 2～4 個完整年度「全年營收 ÷ 同期累計營收」的中位數；不足 2 年改用月平均年化。
- 剩餘營收 ＝ 全年營收 − 已公布季度的財報營收。
- 剩餘 EPS ＝ 剩餘營收 × 近四季淨利率（近四季歸屬母公司淨利 ÷ 近四季營收）÷ 股數。
- 股數 ＝ 最新一季歸屬母公司淨利 ÷ 最新一季 EPS。
- 另外計算「舊版算法」（`reference/backtest/bt.py` 的 old_method）與「近四季 EPS」供回測比較。

**近四季 ROE** ＝ 近四季歸屬母公司淨利 ÷（四季前期末權益＋最新期末權益）÷ 2

**預估股利** ＝ 預估 EPS × 近 5 年平均配發率（現金、股票分開）。某年 EPS ≤ 0 的年度不列入平均。

**外部股東權益報酬率** ＝ 預估 EPS ÷ 股價（等於預估本益比的倒數）

**自由現金流** ＝ 營業現金流 − 資本支出。輸出：近四季每股自由現金流、自由現金流殖利率、近 5 年自由現金流為正的年數。

**其他篩選欄位**：單月營收年增率、累計營收年增率、去年 EPS、EPS 成長率、毛利率、營益率、預估本益比、股價淨值比、預估現金殖利率、近 5 年平均配發率、連續配息年數、盈餘含金量（近四季營業現金流 ÷ 近四季淨利）、負債比。

**可信度**：近 8 季淨利率標準差大或營收年增率波動大者標 `low_confidence: true`。

## 5. data/site/stocks.json

```json
{
  "as_of": "2026-11-18",
  "sources_date": {"monthly_revenue": "2026-10", "quarterly": "2026Q3", "price": "2026-11-17"},
  "stocks": [
    {
      "code": "2330", "name": "台積電", "market": "上市", "industry": "半導體業",
      "flags": {"excluded_industry": false, "loss": false, "ytd_revenue_decline": false, "low_confidence": false},
      "price": 0, "pe": 0, "pb": 0,
      "eps": {"forecast": 0, "last_year": 0, "growth": 0, "published": [{"q":1,"eps":0}], "estimated": [{"q":4,"eps":0,"actual_months":1}],
              "margin_ttm": 0, "seasonal_factor": 0, "shares": 0},
      "roe_ttm": 0, "external_roe": 0,
      "dividend": {"cash": 0, "stock": 0, "cash_ratio": 0, "stock_ratio": 0, "history": [{"y":2025,"eps":0,"cash":0,"stock":0}], "yield": 0},
      "revenue": {"latest_month": "2026-10", "mom_yoy": 0, "ytd_yoy": 0},
      "fcf": {"per_share_ttm": 0, "yield": 0, "positive_years_5y": 0},
      "quality": {"gross_margin": 0, "operating_margin": 0, "cash_conversion": 0, "debt_ratio": 0, "dividend_streak": 0}
    }
  ]
}
```
- 比率一律存小數（0.123 ＝ 12.3%）。缺資料存 `null`，不可補 0。
- `excluded_industry`：產業為營建、金融保險（含銀行、證券、保險、金控）。
- `loss`：近四季 EPS ≤ 0 或今年預估 EPS ≤ 0。
- `ytd_revenue_decline`：累計營收年增率 < 0。
- 檔案目標 < 5 MB。

## 6. 每日更新

- `scripts/update_daily.py`：只抓新期間（新月營收、新季報、最新股價），重算 stocks.json。
- 之後由 Claude 設定 GitHub Actions 排程；本階段只要確保腳本可在 Linux、Python 3.11 無互動執行。

## 7. 完成標準

1. 覆蓋率：上市櫃普通股 ≥ 95% 有完整的最新四季財報與近 24 個月營收；缺漏清單寫進 reports/coverage.md。
2. 抽樣核對：2330、2454、2603、5904、6488 的季營收、EPS、權益、月營收、股利，與公開資訊觀測站逐筆一致（容差 0.5%），結果寫進 reports/validation.md。
3. 全市場回測：用 2024、2025 年、5 月底／8 月底／11 月底三個時點，比較主算法、舊版算法、近四季 EPS 的誤差中位數與平均誤差（排除營建、金融），寫進 reports/backtest.md。
4. 自動測試：計算函式有單元測試，全部通過。

## 8. v1.1 修訂（2026-11-18，依 Codex 兩輪審查）

1. **預估年度**：`target_year`＝已有月營收的最新年度。1～3 月上一年度年報未出前，繼續預估上一年度；網頁一律標示「預估 YYYY 年」，不寫「今年」。
2. **營收口徑**：月營收先用「已公布季度財報營收 ÷ 同期月營收」換算到財報口徑（`revenue_basis_ratio`），全年與各季明細用同一套換算。全年月營收估計不低於已公布月營收累計。比例超出 0.8～1.25 標為可信度低。
3. **自由現金流**：官方彙總表沒有資本支出。`fcf`（營業現金流−資本支出）暫留 null；另輸出 `fcf_approx`＝營業現金流＋投資現金流，網頁標示「簡易自由現金流（近似）」。是否逐家抓 XBRL 取得資本支出，待使用者決定。
4. **股利未知狀態**：資料中最新盈餘年度沒有除權息紀錄 →「尚未配發或資料未到」（null，不列入平均）；較早年度沒有紀錄 → 未配發（0）。連續配息年數從最近已確認年度往前數，輸出 `dividend_streak_through`。
5. **股數基礎**：以「累計母公司淨利 ÷ 累計 EPS」推算股數（|EPS|≥0.3 才推），與最新一季差距超過 20% 時換算到目前股數（`share_factor`）。已知限制：未使用官方股本變動事件，增資當季的加權平均股數、股利除權息日股數無法精確處理。
6. **抓取**：每期存檔前檢查筆數與必要欄位有值比例，不達標就報錯不寫檔；所有抓取指令一律串行執行。
7. **核對容差**：金額 0.5%；每股數字（EPS、股利）四捨五入到小數兩位後須完全相同。除權息實際配發（例：2454 2023 年 55.01 元）與決議數（55 元）的差異屬口徑不同，於報告列明。
8. **回測定位**：使用事後整理的資料，結論是「各算法相對準確度」，不是當時可得的精確誤差。
