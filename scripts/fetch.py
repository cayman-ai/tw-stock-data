"""抓官方資料，存成每期一個檔（data/parts/），可中斷續跑：已抓過的期間不重抓（最新期間會重抓以取得更正）。

用法：
  python scripts/fetch.py companies
  python scripts/fetch.py revenue --months 60
  python scripts/fetch.py is --quarters 20        # 損益表；bs＝資產負債表、cf＝現金流量表
  python scripts/fetch.py dividends --years 6
  python scripts/fetch.py prices
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from twdata import sources  # noqa: E402
from twdata.common import HISTORY, LATEST, PARTS  # noqa: E402


# 完整性門檻：(最少筆數, {必要欄位: 最低非空比例})。不達標就報錯，不寫檔、不覆蓋舊檔。
CHECKS = {
    "companies": (1500, {"name": 0.99, "industry_code": 0.99, "report_type": 0.99, "listed_shares": 0.95}),
    "prices": (1500, {"close": 0.8, "pb": 0.7}),
    "revenue": (500, {"revenue": 0.95, "ytd_revenue": 0.95}),
    "is": (500, {"eps": 0.95, "net_income": 0.9, "revenue": 0.85, "parent_net_income": 0.6}),
    "bs": (500, {"total_assets": 0.95, "total_equity": 0.95, "parent_equity": 0.6, "total_liabilities": 0.95}),
    "cf": (500, {"operating_cash_flow": 0.9, "investing_cash_flow": 0.85}),
    "dividends": (300, {"period": 1.0}),
}


class IncompleteData(RuntimeError):
    pass


def check(kind: str, label: str, rows: list[dict]):
    n_min, req = CHECKS[kind]
    if len(rows) < n_min:
        raise IncompleteData(f"{label}：只有 {len(rows)} 筆，低於門檻 {n_min}，可能官方改版或回傳空資料")
    for col, ratio in req.items():
        ok = sum(1 for r in rows if r.get(col) not in (None, "")) / len(rows)
        if ok < ratio:
            raise IncompleteData(f"{label}：欄位 {col} 只有 {ok:.0%} 有值（門檻 {ratio:.0%}），可能表頭改名")


def save(path: Path, rows: list[dict], kind: str, label: str):
    """先檢查完整性，再寫暫存檔、原子替換，避免留下半套資料。"""
    check(kind, label, rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)


def latest_month(today: date) -> tuple[int, int]:
    """月營收每月 10 日前公布上個月；11 日以後才把上個月視為已公布。"""
    y, m = today.year, today.month - 1
    if today.day <= 10:
        m -= 1
    while m <= 0:
        y, m = y - 1, m + 12
    return y, m


def latest_quarter(today: date) -> tuple[int, int]:
    """一般行業申報期限：Q1 5/15、Q2 8/14、Q3 11/14、年報 3/31。"""
    md = (today.month, today.day)
    if md > (11, 14):
        return today.year, 3
    if md > (8, 14):
        return today.year, 2
    if md > (5, 15):
        return today.year, 1
    if md > (3, 31):
        return today.year - 1, 4
    return today.year - 1, 3


def back(y, p, n, per):
    out = []
    for _ in range(n):
        out.append((y, p))
        p -= 1
        if p == 0:
            y, p = y - 1, per
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["companies", "revenue", "is", "bs", "cf", "dividends", "prices"])
    ap.add_argument("--months", type=int, default=60)
    ap.add_argument("--quarters", type=int, default=20)
    ap.add_argument("--years", type=int, default=6)
    ap.add_argument("--markets", default="sii,otc")
    ap.add_argument("--refresh", type=int, default=1, help="最新幾期一律重抓")
    a = ap.parse_args()
    today = date.today()
    markets = a.markets.split(",")

    if a.what == "companies":
        rows = [r for m in markets for r in sources.companies(m)]
        save(HISTORY / "companies.csv", rows, "companies", "公司資料")
        print(f"公司 {len(rows)} 家")
    elif a.what == "prices":
        rows = sources.prices()
        save(LATEST / "prices.csv", rows, "prices", "股價")
        print(f"股價 {len(rows)} 檔，日期 {rows[0]['date'] if rows else '-'}")
    elif a.what == "revenue":
        periods = back(*latest_month(today), a.months, 12)
        for i, (y, mo) in enumerate(periods):
            for m in markets:
                p = PARTS / "revenue" / f"{m}_{y}{mo:02d}.csv"
                if p.exists() and i >= a.refresh:
                    continue
                rows = sources.monthly_revenue(m, y, mo)
                save(p, rows, "revenue", f"月營收 {m} {y}-{mo:02d}")
                print(f"月營收 {m} {y}-{mo:02d}: {len(rows)} 家", flush=True)
    elif a.what in ("is", "bs", "cf"):
        fn = {"is": sources.income_statement, "bs": sources.balance_sheet, "cf": sources.cash_flow}[a.what]
        periods = back(*latest_quarter(today), a.quarters, 4)
        for i, (y, q) in enumerate(periods):
            for m in markets:
                p = PARTS / a.what / f"{m}_{y}Q{q}.csv"
                if p.exists() and i >= a.refresh:
                    continue
                rows = fn(m, y, q)
                save(p, rows, a.what, f"{a.what} {m} {y}Q{q}")
                print(f"{a.what} {m} {y}Q{q}: {len(rows)} 家", flush=True)
    elif a.what == "dividends":
        for i, y in enumerate(range(today.year, today.year - a.years, -1)):
            for m in markets:
                p = PARTS / "dividends" / f"{m}_{y}.csv"
                if p.exists() and i >= a.refresh + 1:
                    continue
                rows = sources.dividends(m, y)
                save(p, rows, "dividends", f"股利 {m} {y}")
                print(f"股利 {m} 查詢年度 {y}: {len(rows)} 筆", flush=True)


if __name__ == "__main__":
    main()
