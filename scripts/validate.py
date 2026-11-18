"""抽樣核對：把 data/history 的 5 檔資料，和舊站 CT-EPS-Analytics 資料庫（逐家抓公開資訊觀測站 XBRL 財報與月營收頁面建立）逐筆比對。

兩邊來源不同（本專案用官方「彙總表」，舊站用逐家 XBRL），數字一致即可互相印證。
用法：python scripts/validate.py <舊站 sqlite 路徑>
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from twdata.common import HISTORY, ROOT  # noqa: E402

CODES = ["2330", "2454", "2603", "5904", "6488"]
TOL = 0.005


def main(db_path: str):
    c = sqlite3.connect(db_path)
    q = pd.read_csv(HISTORY / "quarterly.csv", dtype={"code": str}).set_index(["code", "year", "quarter"])
    mr = pd.read_csv(HISTORY / "monthly_revenue.csv", dtype={"code": str}).set_index(["code", "year", "month"])
    dv = pd.read_csv(HISTORY / "dividends.csv", dtype={"code": str}).set_index(["code", "earnings_year"])
    lines, fails = [], []

    def cmp(label, code, period, ours, theirs, tol=TOL, absolute=False):
        if ours is None or theirs is None or pd.isna(ours) or pd.isna(theirs):
            return "缺"
        diff = abs(ours - theirs) if absolute else abs(ours - theirs) / max(abs(theirs), 1e-9)
        ok = diff <= tol
        if not ok:
            fails.append(f"| {code} | {label} | {period} | {ours:,.2f} | {theirs:,.2f} |")
        return ok

    stats = {}
    for code in CODES:
        s = stats.setdefault(code, {"季營收": [0, 0], "EPS": [0, 0], "母公司權益": [0, 0], "月營收": [0, 0], "現金股利": [0, 0]})
        for y, qq, rev, eps, eq in c.execute(
                "select report_year,quarter,revenue,basic_eps,parent_equity from quarterly_financials where stock_code=? and basis='single'", (code,)):
            k = (code, y, qq)
            if k not in q.index:
                continue
            row = q.loc[k]
            for lab, ours, theirs, kw in (("季營收", row["revenue"], rev * 1000 if rev is not None else None, {}),
                                          ("EPS", row["eps"], eps, {"tol": 0.011, "absolute": True}),
                                          ("母公司權益", row["parent_equity"], eq * 1000 if eq is not None else None, {})):
                res = cmp(lab, code, f"{y}Q{qq}", ours, theirs, **kw)
                if res != "缺":
                    s[lab][0] += 1
                    s[lab][1] += bool(res)
        for y, mo, rev in c.execute("select revenue_year,revenue_month,current_month_revenue from monthly_revenues where stock_code=?", (code,)):
            k = (code, y, mo)
            if k in mr.index:
                res = cmp("月營收", code, f"{y}-{mo:02d}", mr.loc[k]["revenue"], rev * 1000)
                if res != "缺":
                    s["月營收"][0] += 1
                    s["月營收"][1] += bool(res)
        old, quarterly = {}, {}
        for y, qtr, cash in c.execute("select dividend_year,quarter,total_cash_dividend from dividend_declarations where stock_code=?", (code,)):
            if qtr is not None:
                quarterly[y] = quarterly.get(y, 0) + (cash or 0)
            else:
                old[y] = old.get(y, 0) + (cash or 0)
        old.update(quarterly)   # 有季配息的年度，以各季加總為準
        for y, cash in old.items():
            k = (code, y)
            if k in dv.index:
                res = cmp("現金股利", code, str(y), dv.loc[k]["cash_dividend"], cash, tol=0.011, absolute=True)
                if res != "缺":
                    s["現金股利"][0] += 1
                    s["現金股利"][1] += bool(res)

    lines.append("# 抽樣核對結果\n")
    lines.append("對照來源：舊站 CT-EPS-Analytics 資料庫（逐家抓公開資訊觀測站 XBRL 財報、月營收頁面、股利頁面）。"
                 "本專案改用官方彙總表，兩邊來源不同，數字一致即互相印證。容差：金額 0.5%，EPS 與股利 0.01 元。\n")
    lines.append("| 股票 | 季營收 | EPS | 母公司權益 | 月營收 | 現金股利 |")
    lines.append("|---|---|---|---|---|---|")
    for code, s in stats.items():
        lines.append(f"| {code} | " + " | ".join(f"{v[1]}/{v[0]}" for v in s.values()) + " |")
    lines.append("\n格式：一致筆數／比對筆數。\n")
    if fails:
        lines.append("## 不一致明細\n\n| 股票 | 項目 | 期間 | 本專案 | 舊站 |\n|---|---|---|---|---|")
        lines += fails
    else:
        lines.append("全部一致。")
    out = ROOT / "reports" / "validation.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:12]))
    print(f"不一致 {len(fails)} 筆")


if __name__ == "__main__":
    main(sys.argv[1])
