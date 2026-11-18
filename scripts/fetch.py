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
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from twdata import sources  # noqa: E402
from twdata.common import HISTORY, LATEST, PARTS  # noqa: E402


def save(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


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
        save(HISTORY / "companies.csv", rows)
        print(f"公司 {len(rows)} 家")
    elif a.what == "prices":
        rows = sources.prices()
        save(LATEST / "prices.csv", rows)
        print(f"股價 {len(rows)} 檔，日期 {rows[0]['date'] if rows else '-'}")
    elif a.what == "revenue":
        periods = back(*latest_month(today), a.months, 12)
        for i, (y, mo) in enumerate(periods):
            for m in markets:
                p = PARTS / "revenue" / f"{m}_{y}{mo:02d}.csv"
                if p.exists() and i >= a.refresh:
                    continue
                rows = sources.monthly_revenue(m, y, mo)
                save(p, rows)
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
                save(p, rows)
                print(f"{a.what} {m} {y}Q{q}: {len(rows)} 家", flush=True)
    elif a.what == "dividends":
        for i, y in enumerate(range(today.year, today.year - a.years, -1)):
            for m in markets:
                p = PARTS / "dividends" / f"{m}_{y}.csv"
                if p.exists() and i >= a.refresh + 1:
                    continue
                rows = sources.dividends(m, y)
                save(p, rows)
                print(f"股利 {m} 查詢年度 {y}: {len(rows)} 筆", flush=True)


if __name__ == "__main__":
    main()
