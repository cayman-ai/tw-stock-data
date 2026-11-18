"""逐家抓 XBRL 的資本支出（年初至季末累計）。可中斷續跑：已抓到的（公司, 期間）不重抓。

需要的期間：最新一季、去年同季（算近四季）、近 5 年的第 4 季（＝全年）。
用法：python scripts/fetch_capex.py --minutes 300   # 跑到時間上限就存檔結束，下次接著抓
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pandas as pd  # noqa: E402

from twdata import xbrl  # noqa: E402
from twdata.common import HISTORY, PARTS  # noqa: E402

OUT = PARTS / "capex" / "capex.csv"
FIELDS = ["code", "year", "quarter", "capex_cum", "ocf_cum", "status"]


def needed_periods(latest: tuple[int, int]) -> list[tuple[int, int]]:
    y, q = latest
    ps = [(y, q), (y - 1, q)] + [(yy, 4) for yy in range(y - 1, y - 6, -1)]
    return sorted(set(ps), reverse=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=300)
    ap.add_argument("--codes", default="", help="只抓指定代號（逗號分隔），測試用")
    a = ap.parse_args()
    t0 = time.time()
    q = pd.read_csv(HISTORY / "quarterly.csv", dtype={"code": str}, usecols=["code", "year", "quarter", "eps"]).dropna()
    latest = tuple(q.sort_values(["year", "quarter"]).iloc[-1][["year", "quarter"]].astype(int))
    periods = needed_periods(latest)
    co = pd.read_csv(HISTORY / "companies.csv", dtype={"code": str, "industry_code": str})
    codes = a.codes.split(",") if a.codes else co.loc[co["industry_code"] != "17", "code"].tolist()   # 金融業無資本支出概念，略過

    done = {}
    if OUT.exists():
        old = pd.read_csv(OUT, dtype={"code": str})
        # 「無財報檔」可能只是還沒公布，下次重試；其他狀態視為已完成
        old = old[old["status"] != "無財報檔"]
        done = {(r.code, int(r.year), int(r.quarter)): r._asdict() for r in old.itertuples(index=False)}
    rows = list(done.values())
    todo = [c for c in codes if any((c, y, qq) not in done for y, qq in periods)]
    print(f"需要期間 {periods}；待抓 {len(todo)} 家（已完成 {len(codes) - len(todo)} 家）", flush=True)
    n = 0
    for code in todo:
        if (time.time() - t0) / 60 > a.minutes:
            print("到達時間上限，存檔後結束，下次接著抓", flush=True)
            break
        need = [(y, qq) for y, qq in periods if (code, y, qq) not in done]
        try:
            got = xbrl.capex_for(code, need)
        except Exception as e:  # noqa: BLE001  單一公司失敗不影響其他公司，下次重試
            print(f"  ! {code} 失敗：{e}", flush=True)
            continue
        rows += [r for r in got]
        n += 1
        if n % 25 == 0:
            print(f"已抓 {n} 家，{(time.time() - t0) / 60:.0f} 分鐘", flush=True)
            _save(rows)
    _save(rows)
    left = len(todo) - n
    print(f"本次完成 {n} 家，剩 {left} 家", flush=True)


def _save(rows):
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, OUT)


if __name__ == "__main__":
    main()
