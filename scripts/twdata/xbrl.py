"""逐家抓公開資訊觀測站 XBRL 財報，只取資本支出（取得不動產、廠房及設備）。解析邏輯沿用舊站 legacy/scripts/ingest_xbrl.py。"""
from __future__ import annotations

import html
import json
import re
import xml.etree.ElementTree as ET
from calendar import monthrange
from datetime import date

from .common import fetch

MOPS_REDIRECT = "https://mops.twse.com.tw/mops/api/redirectToOld"
MOPS_DOWNLOAD_HOST = "https://mopsov.twse.com.tw"
CAPEX = ["PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities", "PaymentsToAcquirePropertyPlantAndEquipment",
         "AcquisitionOfPropertyPlantAndEquipment"]
OCF = ["CashFlowsFromUsedInOperatingActivities", "NetCashFlowsFromUsedInOperatingActivities"]


def document_list(code: str) -> dict[tuple[int, int], str]:
    """回傳 {(年, 季): 下載網址}；同一期有合併（C）與個體報表時優先合併。"""
    resp = json.loads(fetch(MOPS_REDIRECT, json_body={"apiName": "ajax_t203sb01", "parameters": {
        "co_id": code, "TYPEK": "all", "encodeURIComponent": 1, "step": 1, "firstin": 1, "off": 1}}))
    listing = fetch(resp["result"]["url"])
    pat = re.compile(r"<td>(\d{3})Q([1-4])</td><td>([^<]+)</td>.*?window\.open\('(/server-java/FileDownLoad\?[^']+)'", re.DOTALL)
    docs, kinds = {}, {}
    for roc_y, q, _label, path in pat.findall(listing):
        params = html.unescape(path)
        rid = (re.search(r"report_id=([^&]+)", params) or [None, ""])[1]
        k = (int(roc_y) + 1911, int(q))
        if k not in docs or rid == "C":
            docs[k], kinds[k] = MOPS_DOWNLOAD_HOST + params, rid
    return docs


def _local(tag):
    return tag.rsplit("}", 1)[-1].split(":")[-1]


def _num(el):
    if el.attrib.get("{http://www.w3.org/2001/XMLSchema-instance}nil") == "true":
        return None
    t = "".join(el.itertext()).strip().replace(",", "").replace(" ", "")
    if not t or t in {"-", "—", "N/A"}:
        return None
    neg = t.startswith("(") and t.endswith(")")
    try:
        v = float(t[1:-1] if neg else t)
    except ValueError:
        return None
    v *= 10 ** int(el.attrib.get("scale", "0") or 0)
    return -v if el.attrib.get("sign") == "-" or neg else v


def cash_items(content: bytes, year: int, quarter: int) -> dict:
    """回傳該期「年初至季末」累計的資本支出（正數、千元）與營業現金流（千元）；查無科目為 None。"""
    root = ET.fromstring(content)
    ctx = {}
    for el in root.iter():
        if _local(el.tag) == "context" and el.attrib.get("id"):
            it = {"s": None, "e": None, "dim": False}
            for ch in el.iter():
                n = _local(ch.tag)
                if n == "startDate":
                    it["s"] = (ch.text or "").strip()
                elif n == "endDate":
                    it["e"] = (ch.text or "").strip()
                elif n in ("explicitMember", "typedMember"):
                    it["dim"] = True
            ctx[el.attrib["id"]] = it
    end = date(year, quarter * 3, monthrange(year, quarter * 3)[1]).isoformat()
    found = {"capex": [], "ocf": []}
    for el in root.iter():
        if _local(el.tag) != "nonFraction":
            continue
        name = (el.attrib.get("name") or "").split(":")[-1]
        c = ctx.get(el.attrib.get("contextRef"))
        if not c or c["dim"] or c["e"] != end or not c["s"] or not c["s"].startswith(str(year)):
            continue
        for key, aliases in (("capex", CAPEX), ("ocf", OCF)):
            if name in aliases:
                v = _num(el)
                if v is not None:
                    found[key].append((aliases.index(name), c["s"], v))
    out = {}
    for key in found:
        f = sorted(found[key], key=lambda x: (x[0], x[1]))   # 別名優先、最早起始日（＝年初累計）
        out[key] = (abs(f[0][2]) if key == "capex" else f[0][2]) / 1000 if f else None
    return out


def capex_for(code: str, periods: list[tuple[int, int]]) -> list[dict]:
    docs = document_list(code)
    rows = []
    for y, q in periods:
        url = docs.get((y, q))
        if not url:
            rows.append({"code": code, "year": y, "quarter": q, "capex_cum": None, "ocf_cum": None, "status": "無財報檔"})
            continue
        try:
            items = cash_items(fetch(url, raw=True), y, q)
            rows.append({"code": code, "year": y, "quarter": q, "capex_cum": items["capex"], "ocf_cum": items["ocf"],
                         "status": "ok" if items["capex"] is not None else "查無資本支出科目"})
        except ET.ParseError:
            rows.append({"code": code, "year": y, "quarter": q, "capex_cum": None, "ocf_cum": None, "status": "財報檔無法解析"})
    return rows
