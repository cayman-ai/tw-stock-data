from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sqlite3
import sys
import time
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path


PARSER_VERSION = "monthly-revenue-v1"
MOPS_REDIRECT = "https://mops.twse.com.tw/mops/api/redirectToOld"
LATEST_ENDPOINTS = (
    "https://openapi.twse.com.tw/v1/opendata/t187ap05_L",
    "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O",
)
USER_AGENT = "Mozilla/5.0 CT-EPS-Analytics/0.3"


def http_request(url: str, *, body: bytes | None = None, content_type: str | None = None) -> bytes:
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "zh-TW,zh;q=0.9"}
    if content_type:
        headers["Content-Type"] = content_type
        headers["Accept"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method="POST" if body else "GET")
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                return response.read()
        except Exception as exc:  # network errors are retried; parser/database errors are not
            last_error = exc
            if attempt < 2:
                time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"資料來源連線失敗：{last_error}")


def code_of(row: dict) -> str:
    return str(row.get("公司代號") or row.get("SecuritiesCompanyCode") or row.get("Code") or "").strip()


def latest_period(stock_code: str) -> tuple[int, int]:
    for endpoint in LATEST_ENDPOINTS:
        rows = json.loads(http_request(endpoint))
        match = next((row for row in rows if code_of(row) == stock_code), None)
        if not match:
            continue
        raw = str(match.get("資料年月") or match.get("YearMonth") or "").strip()
        digits = re.sub(r"\D", "", raw)
        if len(digits) >= 5:
            roc_year, month = int(digits[:-2]), int(digits[-2:])
            if 1 <= month <= 12:
                return roc_year + 1911, month
    today = date.today()
    month = today.month - 1
    return (today.year - 1, 12) if month == 0 else (today.year, month)


def subtract_months(year: int, month: int, offset: int) -> tuple[int, int]:
    serial = year * 12 + month - 1 - offset
    return serial // 12, serial % 12 + 1


def mops_month(stock_code: str, year: int, month: int) -> tuple[str, bytes]:
    roc_year = year - 1911
    payload = json.dumps(
        {
            "apiName": "ajax_t05st10_ifrs",
            "parameters": {
                "co_id": stock_code,
                "TYPEK": "all",
                "encodeURIComponent": 1,
                "step": 1,
                "firstin": "true",
                "off": 1,
                "isnew": "false",
                "year": str(roc_year),
                "month": f"{month:02d}",
                "queryName": "co_id",
                "inpuType": "co_id",
            },
        },
        ensure_ascii=False,
    ).encode("utf-8")
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            # 每次重試都重新取得加密網址；舊網址在暫時轉址後可能已失效。
            response = json.loads(http_request(MOPS_REDIRECT, body=payload, content_type="application/json"))
            if response.get("code") != 200 or not response.get("result", {}).get("url"):
                raise RuntimeError(f"MOPS 查詢失敗：{response.get('message', '未知錯誤')}")
            source_url = response["result"]["url"]
            return source_url, http_request(source_url)
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"MOPS 月營收下載失敗：{last_error}")


def clean_text(fragment: str) -> str:
    fragment = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.IGNORECASE)
    fragment = re.sub(r"<[^>]+>", "", fragment)
    return " ".join(html.unescape(fragment).replace("\xa0", " ").split())


def parse_number(text: str) -> float | None:
    cleaned = clean_text(text).replace(",", "")
    if not cleaned or cleaned in {"-", "—", "N/A"}:
        return None
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    if negative:
        cleaned = cleaned[1:-1]
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return -value if negative else value


def parse_monthly_revenue(content: bytes, expected_year: int, expected_month: int) -> dict:
    page = content.decode("utf-8", errors="replace")
    if "年度及月份 欄位錯誤" in page or "查無資料" in page:
        raise RuntimeError(f"MOPS 查無 {expected_year}-{expected_month:02d} 月營收")
    heading = re.search(r"民國(\d{3})年(\d{2})月", page)
    if not heading:
        raise RuntimeError("無法辨識 MOPS 月營收期別")
    actual_year, actual_month = int(heading.group(1)) + 1911, int(heading.group(2))
    if (actual_year, actual_month) != (expected_year, expected_month):
        raise RuntimeError(f"MOPS 回傳期別 {actual_year}-{actual_month:02d}，與查詢期別不一致")

    table_match = re.search(r"<TABLE[^>]*class=['\"]hasBorder['\"][^>]*>(.*?)</table>", page, re.IGNORECASE | re.DOTALL)
    if not table_match:
        raise RuntimeError("無法辨識 MOPS 月營收資料表")
    rows = re.findall(r"<TR[^>]*>(.*?)</TR>", table_match.group(1), re.IGNORECASE | re.DOTALL)
    values: list[float | None] = []
    for row in rows:
        cells = re.findall(r"<(?:TH|TD)[^>]*>(.*?)</(?:TH|TD)>", row, re.IGNORECASE | re.DOTALL)
        if len(cells) >= 2 and clean_text(cells[0]) in {"本月", "去年同期", "增減金額", "增減百分比", "本年累計", "去年累計"}:
            values.append(parse_number(cells[1]))
    if len(values) < 8:
        raise RuntimeError(f"月營收欄位不完整：僅解析到 {len(values)} 欄")
    note_match = re.search(r"備註\s*/\s*營收變化原因說明</TH><TD[^>]*>(.*?)</TD>", page, re.IGNORECASE | re.DOTALL)
    note = clean_text(note_match.group(1)) if note_match else ""
    yoy = None if values[3] is not None and abs(values[3]) >= 999999.99 else values[3]
    ytd_yoy = None if values[7] is not None and abs(values[7]) >= 999999.99 else values[7]
    return {
        "current_month_revenue": values[0] / 1000 if values[0] is not None else None,
        "prior_year_month_revenue": values[1] / 1000 if values[1] is not None else None,
        "month_change_amount": values[2] / 1000 if values[2] is not None else None,
        "yoy_percent": yoy,
        "year_to_date_revenue": values[4] / 1000 if values[4] is not None else None,
        "prior_year_to_date_revenue": values[5] / 1000 if values[5] is not None else None,
        "ytd_change_amount": values[6] / 1000 if values[6] is not None else None,
        "ytd_yoy_percent": ytd_yoy,
        "note": note,
    }


def quality(record: dict) -> tuple[str, str]:
    notes: list[str] = []
    required = ("current_month_revenue", "prior_year_month_revenue", "year_to_date_revenue")
    missing = [field for field in required if record.get(field) is None]
    if missing:
        notes.append("缺少欄位：" + "、".join(missing))
    pairs = (
        ("current_month_revenue", "prior_year_month_revenue", "month_change_amount", "本月差額"),
        ("year_to_date_revenue", "prior_year_to_date_revenue", "ytd_change_amount", "累計差額"),
    )
    for current, previous, change, label in pairs:
        if all(record.get(key) is not None for key in (current, previous, change)):
            difference = record[current] - record[previous] - record[change]
            if abs(difference) > 0.011:
                notes.append(f"{label}勾稽差異 {difference:.3f} 百萬元")
    return ("ok" if not notes else "review", "；".join(notes))


def ingest_company(code: str, months: int, db_path: Path, raw_dir: Path, schema_path: Path, delay: float = 0.3) -> dict:
    end_year, end_month = latest_period(code)
    periods = [subtract_months(end_year, end_month, offset) for offset in range(months)]
    raw_company = raw_dir / code
    raw_company.mkdir(parents=True, exist_ok=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(schema_path.read_text(encoding="utf-8"))
    ingested = []
    try:
        for index, (year, month) in enumerate(reversed(periods)):
            source_url, content = mops_month(code, year, month)
            record = parse_monthly_revenue(content, year, month)
            digest = hashlib.sha256(content).hexdigest()
            raw_path = raw_company / f"{code}-{year}-{month:02d}.html"
            raw_path.write_bytes(content)
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "DELETE FROM monthly_revenues WHERE stock_code=? AND revenue_year=? AND revenue_month=?",
                (code, year, month),
            )
            conn.execute(
                "DELETE FROM monthly_revenue_documents WHERE stock_code=? AND revenue_year=? AND revenue_month=?",
                (code, year, month),
            )
            cursor = conn.execute(
                """
                INSERT INTO monthly_revenue_documents
                (stock_code, revenue_year, revenue_month, source_url, fetched_at, sha256, raw_path, parser_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (code, year, month, source_url, now, digest, str(raw_path.relative_to(db_path.parent.parent)), PARSER_VERSION),
            )
            status, notes = quality(record)
            conn.execute(
                """
                INSERT INTO monthly_revenues (
                  stock_code, revenue_year, revenue_month, current_month_revenue,
                  prior_year_month_revenue, month_change_amount, yoy_percent,
                  year_to_date_revenue, prior_year_to_date_revenue, ytd_change_amount,
                  ytd_yoy_percent, note, source_document_id, quality_status, quality_notes, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    code, year, month, record["current_month_revenue"], record["prior_year_month_revenue"],
                    record["month_change_amount"], record["yoy_percent"], record["year_to_date_revenue"],
                    record["prior_year_to_date_revenue"], record["ytd_change_amount"], record["ytd_yoy_percent"],
                    record["note"], cursor.lastrowid, status, notes, now,
                ),
            )
            conn.commit()
            ingested.append({"year": year, "month": month, "revenue": record["current_month_revenue"], "sha256": digest})
            if index + 1 < len(periods):
                time.sleep(max(0.0, delay))
        conn.execute("PRAGMA optimize")
        conn.commit()
    finally:
        conn.close()
    return {
        "stockCode": code,
        "months": len(ingested),
        "startPeriod": f"{ingested[0]['year']}-{ingested[0]['month']:02d}",
        "endPeriod": f"{ingested[-1]['year']}-{ingested[-1]['month']:02d}",
        "database": str(db_path),
        "rawDirectory": str(raw_company),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="擷取 MOPS 24～36 個月月營收歷史")
    parser.add_argument("stock_codes", nargs="+", help="股票代號，例如 2330 6488")
    parser.add_argument("--months", type=int, default=36, help="每家公司擷取月數，預設 36")
    parser.add_argument("--db", default="data/ct_eps.sqlite")
    parser.add_argument("--raw-dir", default="data/raw/monthly-revenue")
    parser.add_argument("--delay", type=float, default=0.3, help="每月查詢間隔秒數，預設 0.3")
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    db_path = (project / args.db).resolve()
    raw_dir = (project / args.raw_dir).resolve()
    schema_path = project / "db" / "schema.sql"
    results = []
    for code in args.stock_codes:
        if not re.fullmatch(r"[0-9A-Za-z]{4,6}", code):
            raise SystemExit(f"無效股票代號：{code}")
        results.append(ingest_company(code, max(1, min(args.months, 60)), db_path, raw_dir, schema_path, args.delay))
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"月營收擷取失敗：{exc}", file=sys.stderr)
        raise
