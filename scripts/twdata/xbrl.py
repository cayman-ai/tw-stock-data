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


# ---------- 資產負債表（逐家）：合約負債、存貨、應收帳款 ----------
BS_URL = "https://mopsov.twse.com.tw/mops/web/ajax_t164sb03"
_DASH = r"[－\-—–‐]"
BS_LABELS = {
    "cl_current": re.compile(rf"^合約負債{_DASH}?流動$"),
    "cl_noncurrent": re.compile(rf"^合約負債{_DASH}?非流動$"),
    "inventory": re.compile(r"^存貨(淨額)?$"),
    "receivables": re.compile(rf"^應收帳款(淨額)?$|^應收帳款{_DASH}?關係人(淨額)?$"),   # 一般＋關係人加總
    "total_liabilities": re.compile(r"^負債(總額|總計|合計)$"),
    "total_assets": re.compile(r"^資產(總額|總計|合計)$"),
}
BS_FIELDS = list(BS_LABELS)


def _cells(tr) -> list[str]:
    return [re.sub(r"[\s　]", "", c.text_content()) for c in tr.xpath("./th|./td")]


def parse_balance(html: str, year: int, quarter: int) -> dict[tuple[int, int], dict] | str:
    """解析簡明資產負債表：第一欄是本期末，最後一欄是去年同期末（第 4 季頁面只有本年底與去年底兩欄）。
    每個日期欄都是「金額＋百分比」兩格。回傳 {(年, 季): {欄位: 千元}}；科目不存在的欄位為 None（財報未單獨列示）。"""
    from lxml import html as lh
    if "查無" in html or "不存在" in html:
        return "查無資料"
    try:
        doc = lh.fromstring(html)
    except Exception:  # noqa: BLE001
        return "頁面格式不符"
    rows = [_cells(tr) for tr in doc.iter("tr")]
    head = next((r for r in rows if r and r[0] == "會計項目"), None)
    if not head:
        return "頁面格式不符"
    dates = re.findall(r"(\d{2,3})年(\d{2})月(\d{2})日", "".join(head[1:]))
    end_md = {1: "0331", 2: "0630", 3: "0930", 4: "1231"}[quarter]
    if len(dates) < 2 or (int(dates[0][0]) + 1911, dates[0][1] + dates[0][2]) != (year, end_md) \
            or (int(dates[-1][0]) + 1911, dates[-1][1] + dates[-1][2]) != (year - 1, end_md):
        return "頁面格式不符"      # 期別對不上（例如非曆年制）就不硬套
    n = len(dates)
    cur = dict.fromkeys(BS_FIELDS)
    prev = dict.fromkeys(BS_FIELDS)
    for r in rows:
        if len(r) != 1 + 2 * n:
            continue
        for k, pat in BS_LABELS.items():
            if pat.match(r[0]):
                a, b = num(r[1]), num(r[1 + 2 * (n - 1)])
                if k == "receivables":   # 一般與關係人加總
                    cur[k] = (cur[k] or 0) + a if a is not None else cur[k]
                    prev[k] = (prev[k] or 0) + b if b is not None else prev[k]
                elif cur[k] is None and prev[k] is None:
                    cur[k], prev[k] = a, b
                break
    if cur["total_liabilities"] is None:
        return "頁面格式不符"
    return {(year, quarter): cur, (year - 1, quarter): prev}


def balance_query(code: str, year: int, quarter: int) -> dict[tuple[int, int], dict] | str:
    html = fetch(BS_URL, dict(encodeURIComponent=1, step=1, firstin=1, off=1, queryName="co_id", inpuType="co_id",
                              TYPEK="all", isnew="false", co_id=code, year=str(year - 1911), season=f"{quarter:02d}"))
    return parse_balance(html, year, quarter)
