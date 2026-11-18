"""逐家抓公開資訊觀測站「現金流量表」網頁（ajax_t164sb05），取資本支出（取得不動產、廠房及設備）。

一頁同時有「本期」與「去年同期」兩欄（年初至季末累計，千元），所以：
  查最新一季 → 得到今年與去年同季；查某年第 4 季 → 得到該年與前一年全年。
（原本逐份下載 XBRL 檔每份約 40 秒，全市場要 160 小時，改用此網頁每次 2～6 秒。）
"""
from __future__ import annotations

import re

from .common import fetch, num, read_tables, flatten

URL = "https://mopsov.twse.com.tw/mops/web/ajax_t164sb05"
LABELS = re.compile(r"^(取得|購置)不動產[、，,]?廠房及設備$")


def capex_query(code: str, year: int, quarter: int) -> dict[tuple[int, int], float | None] | str:
    """回傳 {(年, 季): 資本支出（正數，千元）}；失敗回傳原因字串。"""
    html = fetch(URL, dict(encodeURIComponent=1, step=1, firstin=1, off=1, queryName="co_id", inpuType="co_id",
                           TYPEK="all", isnew="false", co_id=code, year=str(year - 1911), season=f"{quarter:02d}"))
    if "查無" in html or "不存在" in html:
        return "查無資料"
    for t in read_tables(html):
        t = flatten(t)
        if t.shape[1] < 3:
            continue
        first = t.iloc[:, 0].astype(str).str.replace(r"[\s　]", "", regex=True)
        hit = t[first.str.match(LABELS)]
        if hit.empty:
            continue
        row = hit.iloc[0]
        cur, prev = num(row.iloc[1]), num(row.iloc[2])
        return {(year, quarter): abs(cur) if cur is not None else None,
                (year - 1, quarter): abs(prev) if prev is not None else None}
    if "營業活動之現金流量" in html or "營業活動" in html:
        return "現金流量表沒有資本支出科目"      # 例如當期確實沒有購置，或科目名稱不同
    return "頁面格式不符"
