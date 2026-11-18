"""逐家抓現金流量表網頁的資本支出（年初至季末累計）。可中斷續跑：已抓到的（公司, 期間）不重抓。

每家查 4 次：最新一季（含去年同季）、去年第 4 季（含前年）、3 年前第 4 季、5 年前第 4 季 → 近四季與近 5～6 年全年。
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
from twdata import common  # noqa: E402
from twdata.common import HISTORY, PARTS  # noqa: E402

OUT = PARTS / "capex" / "capex.csv"
FIELDS = ["code", "year", "quarter", "capex_cum", "status"]


def queries(latest: tuple[int, int]) -> list[tuple[int, int]]:
    """要查的（年, 季）；每次查詢同時得到該期與去年同期。最新一季若是第 4 季，就不用另查去年第 4 季。"""
    y, q = latest
    base = [(y, q)] + [(yy, 4) for yy in ((y - 1, y - 3, y - 5) if q != 4 else (y - 2, y - 4))]
    return base


def covered(qs):
    return sorted({p for y, q in qs for p in ((y, q), (y - 1, q))}, reverse=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=300)
    ap.add_argument("--codes", default="", help="只抓指定代號（逗號分隔），測試用")
    ap.add_argument("--delay", type=float, default=3.0, help="每次請求最少間隔秒數（逐家查詢較密集，預設 3 秒）")
    a = ap.parse_args()
    common.DELAY = a.delay
    t0 = time.time()
    q = pd.read_csv(HISTORY / "quarterly.csv", dtype={"code": str}, usecols=["code", "year", "quarter", "eps"]).dropna()
    latest = tuple(q.sort_values(["year", "quarter"]).iloc[-1][["year", "quarter"]].astype(int))
    qs = queries(latest)
    periods = covered(qs)
    co = pd.read_csv(HISTORY / "companies.csv", dtype={"code": str, "industry_code": str})
    codes = a.codes.split(",") if a.codes else co.loc[co["industry_code"] != "17", "code"].tolist()   # 金融業無資本支出概念，略過

    done = {}
    if OUT.exists():
        old = pd.read_csv(OUT, dtype={"code": str})
        # 「查無資料／頁面格式不符」可能是還沒公布或暫時錯誤，下次重試；其他狀態視為已完成
        old = old[~old["status"].isin(["查無資料", "頁面格式不符"])]
        done = {(r.code, int(r.year), int(r.quarter)): r._asdict() for r in old.itertuples(index=False)}
    rows = list(done.values())
    todo = [c for c in codes if any((c, y, qq) not in done for y, qq in periods)]
    print(f"查詢 {qs} → 涵蓋 {periods}；待抓 {len(todo)} 家（已完成 {len(codes) - len(todo)} 家）", flush=True)
    n = 0
    for code in todo:
        if (time.time() - t0) / 60 > a.minutes:
            print("到達時間上限，存檔後結束，下次接著抓", flush=True)
            break
        got = []
        try:
            for y, qq in qs:
                if (code, y, qq) in done and (code, y - 1, qq) in done:
                    continue
                res = xbrl.capex_query(code, y, qq)
                if isinstance(res, str):
                    got += [{"code": code, "year": yy, "quarter": qq, "capex_cum": None, "status": res} for yy in (y, y - 1)]
                else:
                    got += [{"code": code, "year": yy, "quarter": q2, "capex_cum": v, "status": "ok" if v is not None else "空白"}
                            for (yy, q2), v in res.items()]
        except Exception as e:  # noqa: BLE001  單一公司失敗不影響其他公司，下次重試
            print(f"  ! {code} 失敗：{e}", flush=True)
            continue
        for g in got:
            done[(g["code"], g["year"], g["quarter"])] = g
        rows = list(done.values())
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
