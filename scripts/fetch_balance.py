"""逐家抓資產負債表的合約負債（流動、非流動）、存貨、應收帳款。可中斷續跑：已抓到的（公司, 期別）不重抓。

每家只查最新一季一次：頁面同時有「本期末」與「去年同期末」，年增率直接可算。
金融業（產業代號 17）資產負債表格式不同、沒有存貨與合約負債概念，略過。
用法：python scripts/fetch_balance.py --minutes 290   # 跑到時間上限就存檔結束，下次接著抓
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

OUT = PARTS / "balance" / "balance.csv"
FIELDS = ["code", "year", "quarter", "query_year", "query_quarter", *xbrl.BS_FIELDS, "status"]
RETRY = {"查無資料", "頁面格式不符"}   # 可能是還沒公布或暫時錯誤，下次重試


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=290)
    ap.add_argument("--codes", default="", help="只抓指定代號（逗號分隔），測試用")
    ap.add_argument("--delay", type=float, default=3.0, help="每次請求最少間隔秒數")
    a = ap.parse_args()
    common.DELAY = a.delay
    t0 = time.time()
    q = pd.read_csv(HISTORY / "quarterly.csv", dtype={"code": str}, usecols=["code", "year", "quarter", "eps"]).dropna()
    y, qq = tuple(q.sort_values(["year", "quarter"]).iloc[-1][["year", "quarter"]].astype(int))
    co = pd.read_csv(HISTORY / "companies.csv", dtype={"code": str, "industry_code": str})
    codes = a.codes.split(",") if a.codes else co.loc[co["industry_code"] != "17", "code"].tolist()

    rows = []
    if OUT.exists():
        rows = pd.read_csv(OUT, dtype={"code": str}).to_dict("records")
    # 以「查詢期別」判斷是否已完成；重試狀態的那次查詢整筆丟掉重抓
    done_q = {(r["code"], int(r["query_year"]), int(r["query_quarter"])) for r in rows if r["status"] not in RETRY}
    rows = [r for r in rows if (r["code"], int(r["query_year"]), int(r["query_quarter"])) in done_q]
    todo = [c for c in codes if (c, y, qq) not in done_q]
    print(f"查詢 {y}Q{qq}（含去年同季）；待抓 {len(todo)} 家（已完成 {len(codes) - len(todo)} 家）", flush=True)
    n = 0
    for code in todo:
        if (time.time() - t0) / 60 > a.minutes:
            print("到達時間上限，存檔後結束，下次接著抓", flush=True)
            break
        try:
            res = xbrl.balance_query(code, y, qq)
        except Exception as e:  # noqa: BLE001  單一公司失敗不影響其他公司，下次重試
            print(f"  ! {code} 失敗：{e}", flush=True)
            continue
        base = {"code": code, "query_year": y, "query_quarter": qq}
        if isinstance(res, str):
            rows.append({**base, "year": y, "quarter": qq, "status": res})
        else:
            rows += [{**base, "year": yy, "quarter": q2, **vals, "status": "ok"} for (yy, q2), vals in res.items()]
        n += 1
        if n % 25 == 0:
            print(f"已抓 {n} 家，{(time.time() - t0) / 60:.0f} 分鐘", flush=True)
            _save(rows)
    _save(rows)
    print(f"本次完成 {n} 家，剩 {len(todo) - n} 家", flush=True)


def _save(rows):
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows({k: ("" if isinstance(v, float) and v != v else v) for k, v in r.items()} for r in rows)   # NaN 寫成空白
    os.replace(tmp, OUT)


if __name__ == "__main__":
    main()
