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
from datetime import datetime, timezone
from pathlib import Path


PARSER_VERSION = "equity-events-v1"
MOPS_REDIRECT = "https://mops.twse.com.tw/mops/api/redirectToOld"
MOPS_BASE = "https://mops.twse.com.tw/mops/#/web/"
USER_AGENT = "Mozilla/5.0 CT-EPS-Analytics/0.6"


def request_bytes(url: str, body: bytes | None = None) -> bytes:
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "zh-TW,zh;q=0.9"}
    if body is not None:
        headers.update({"Content-Type": "application/json", "Accept": "application/json"})
    req = urllib.request.Request(url, data=body, headers=headers, method="POST" if body else "GET")
    with urllib.request.urlopen(req, timeout=90) as response:
        return response.read()


def mops_call(api_name: str, code: str, parameters: dict) -> tuple[bytes, str]:
    base = {
        "step": 1, "firstin": "true", "off": 1, "TYPEK": "all", "co_id": code,
        "queryName": "co_id", "inpuType": "co_id", "encodeURIComponent": 1,
    }
    base.update(parameters)
    payload = json.dumps({"apiName": api_name, "parameters": base}, ensure_ascii=False).encode("utf-8")
    redirect = json.loads(request_bytes(MOPS_REDIRECT, payload))
    source_url = redirect["result"]["url"]
    return request_bytes(source_url), source_url


def clean(value: str) -> str:
    value = re.sub(r"<br\s*/?>", " ", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def rows(content: bytes) -> list[list[str]]:
    page = content.decode("utf-8", errors="replace")
    result = []
    for raw in re.findall(r"<tr[^>]*>(.*?)</tr>", page, flags=re.I | re.S):
        cells = re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", raw, flags=re.I | re.S)
        if cells:
            result.append([clean(cell) for cell in cells])
    return result


def pairs(parsed_rows: list[list[str]]) -> dict[str, str]:
    result = {}
    for row in parsed_rows:
        for index in range(0, len(row) - 1, 2):
            key, value = row[index].strip(), row[index + 1].strip()
            if key:
                result[key] = value
    return result


def number(value: str | None) -> float | None:
    text = str(value or "").replace(",", "").replace("％", "%").strip()
    if not text or text in {"-", "—", "無", "不適用"}:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(match.group()) if match else None


def roc_date(value: str) -> str | None:
    digits = re.sub(r"\D", "", value)
    if len(digits) == 7:
        return f"{int(digits[:3]) + 1911:04d}-{digits[3:5]}-{digits[5:7]}"
    match = re.search(r"(\d{2,3})[/.-](\d{1,2})[/.-](\d{1,2})", value)
    if not match:
        return None
    return f"{int(match.group(1)) + 1911:04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"


def find_value(items: dict[str, str], *needles: str) -> str:
    for key, value in items.items():
        if all(needle in key for needle in needles):
            return value
    return ""


def find_in_rows(parsed_rows: list[list[str]], *needles: str) -> str:
    fallback = ""
    for row in parsed_rows:
        for index, cell in enumerate(row[:-1]):
            if all(needle in cell for needle in needles):
                fallback = row[index + 1]
                if fallback:
                    return fallback
    return fallback


def save_document(conn: sqlite3.Connection, code: str, source_type: str, stable_url: str,
                  content: bytes, raw_path: Path, project: Path) -> int:
    digest = hashlib.sha256(content).hexdigest()
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(content)
    conn.execute("""
      INSERT OR IGNORE INTO dilution_documents
      (stock_code,source_type,source_url,fetched_at,sha256,raw_path,parser_version)
      VALUES (?,?,?,?,?,?,?)
    """, (code, source_type, stable_url, datetime.now(timezone.utc).isoformat(), digest,
          str(raw_path.relative_to(project)), PARSER_VERSION))
    return conn.execute("""
      SELECT id FROM dilution_documents WHERE stock_code=? AND source_type=? AND sha256=?
    """, (code, source_type, digest)).fetchone()[0]


def upsert_event(conn: sqlite3.Connection, code: str, event: dict) -> None:
    conn.execute("""
      INSERT INTO dilution_events
      (stock_code,event_key,event_type,event_date,effective_date,description,currency,issue_amount,
       outstanding_amount,conversion_price,potential_shares,dilution_percent,status,source_url,
       source_document_id,quality_status,quality_notes,updated_at)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
      ON CONFLICT(stock_code,event_key) DO UPDATE SET
       event_type=excluded.event_type,event_date=excluded.event_date,effective_date=excluded.effective_date,
       description=excluded.description,currency=excluded.currency,issue_amount=excluded.issue_amount,
       outstanding_amount=excluded.outstanding_amount,conversion_price=excluded.conversion_price,
       potential_shares=excluded.potential_shares,dilution_percent=excluded.dilution_percent,
       status=excluded.status,source_url=excluded.source_url,source_document_id=excluded.source_document_id,
       quality_status=excluded.quality_status,quality_notes=excluded.quality_notes,updated_at=excluded.updated_at
    """, (
        code, event["event_key"], event["event_type"], event.get("event_date"), event.get("effective_date"),
        event["description"], event.get("currency", "TWD"), event.get("issue_amount"),
        event.get("outstanding_amount"), event.get("conversion_price"), event.get("potential_shares"),
        event.get("dilution_percent"), event.get("status", ""), event["source_url"],
        event.get("source_document_id"), event.get("quality_status", "ok"), event.get("quality_notes", ""),
        datetime.now(timezone.utc).isoformat(),
    ))


def latest_share_count(conn: sqlite3.Connection, code: str) -> float | None:
    row = conn.execute("""
      SELECT implied_average_shares FROM annual_fundamentals
      WHERE stock_code=? AND implied_average_shares IS NOT NULL
      ORDER BY fiscal_year DESC LIMIT 1
    """, (code,)).fetchone()
    return row[0] * 1_000_000 if row else None


def ingest_restricted_stock(conn: sqlite3.Connection, code: str, years: int, project: Path) -> int:
    content, _ = mops_call("ajax_t160sb01", code, {"isnew": "false"})
    index_path = project / "data" / "raw" / "dilution" / code / "restricted-stock-index.html"
    save_document(conn, code, "restricted_stock:index", MOPS_BASE + "t160sb01", content, index_path, project)
    page = content.decode("utf-8", errors="replace")
    approval_codes = re.findall(r"ALLOW_DATE\.value=\"(\d{7})\"", page)
    cutoff = datetime.now().year - years
    shares_base = latest_share_count(conn, code)
    count = 0
    for approval_code in approval_codes:
        event_date = roc_date(approval_code)
        if not event_date or int(event_date[:4]) < cutoff:
            continue
        detail, _ = mops_call("ajax_t160sb01", code, {"step": 2, "ALLOW_DATE": approval_code})
        path = project / "data" / "raw" / "dilution" / code / f"restricted-stock-{approval_code}.html"
        document_id = save_document(conn, code, f"restricted_stock:{approval_code}", MOPS_BASE + "t160sb01", detail, path, project)
        values = pairs(rows(detail))
        planned = number(find_value(values, "預定發行總數"))
        impact = find_value(values, "每股盈餘稀釋") or find_value(values, "可能費用化")
        dilution = planned / shares_base if planned and shares_base else None
        upsert_event(conn, code, {
            "event_key": f"restricted_stock:{approval_code}", "event_type": "restricted_stock",
            "event_date": event_date,
            "description": f"限制員工權利新股核准案；預定發行 {planned:,.0f} 股" if planned else "限制員工權利新股核准案",
            "potential_shares": planned / 1_000_000 if planned else None,
            "dilution_percent": dilution, "status": "主管機關核准", "source_url": MOPS_BASE + "t160sb01",
            "source_document_id": document_id, "quality_status": "ok" if planned else "review",
            "quality_notes": impact[:500] if impact else "未擷取到公告 EPS 稀釋說明",
        })
        count += 1
        time.sleep(0.1)
    return count


def ingest_employee_options(conn: sqlite3.Connection, code: str, years: int, project: Path) -> int:
    content, _ = mops_call("ajax_t47sb09", code, {})
    index_path = project / "data" / "raw" / "dilution" / code / "employee-options-index.html"
    save_document(conn, code, "employee_options:index", MOPS_BASE + "t47sb09", content, index_path, project)
    page = content.decode("utf-8", errors="replace")
    approvals = re.findall(r"allow_date\.value=\"(\d{8})\"", page)
    cutoff = datetime.now().year - years
    shares_base = latest_share_count(conn, code)
    count = 0
    for approval in approvals:
        if int(approval[:4]) < cutoff:
            continue
        detail, _ = mops_call("ajax_t47sb09", code, {"step": 2, "allow_date": approval, "type": ""})
        path = project / "data" / "raw" / "dilution" / code / f"employee-options-{approval}.html"
        document_id = save_document(conn, code, f"employee_options:{approval}", MOPS_BASE + "t47sb09", detail, path, project)
        values = pairs(rows(detail))
        planned = number(find_value(values, "預定發行總數"))
        reported_rate = number(find_value(values, "占已發行", "比率"))
        dilution = reported_rate / 100 if reported_rate is not None else (planned / shares_base if planned and shares_base else None)
        upsert_event(conn, code, {
            "event_key": f"employee_options:{approval}", "event_type": "employee_stock_option",
            "event_date": approval[:4] + "-" + approval[4:6] + "-" + approval[6:8],
            "description": f"員工認股權憑證核准案；預定可認購 {planned:,.0f} 股" if planned else "員工認股權憑證核准案",
            "potential_shares": planned / 1_000_000 if planned else None, "dilution_percent": dilution,
            "status": "主管機關核准", "source_url": MOPS_BASE + "t47sb09", "source_document_id": document_id,
            "quality_status": "ok" if planned else "review", "quality_notes": find_value(values, "對股權可能稀釋")[:500],
        })
        count += 1
        time.sleep(0.1)
    return count


def ingest_capital_registrations(conn: sqlite3.Connection, code: str, years: int, project: Path) -> int:
    content, _ = mops_call("ajax_t05st05", code, {})
    index_path = project / "data" / "raw" / "dilution" / code / "capital-registration-index.html"
    save_document(conn, code, "capital_registration:index", MOPS_BASE + "t05st05", content, index_path, project)
    page = content.decode("utf-8", errors="replace")
    periods = re.findall(r'year\.value\s*=\s*"(\d{4})";.*?month\.value="(\d{1,2})"', page, flags=re.S)
    cutoff = datetime.now().year - years
    records = []
    for year, month in periods:
        if int(year) < cutoff:
            continue
        detail, _ = mops_call("ajax_t05st05", code, {"step": 2, "year": year, "month": month})
        path = project / "data" / "raw" / "dilution" / code / f"capital-registration-{year}-{int(month):02d}.html"
        document_id = save_document(conn, code, f"capital_registration:{year}-{int(month):02d}", MOPS_BASE + "t05st05", detail, path, project)
        parsed = rows(detail)
        paid_shares = number(find_in_rows(parsed, "實收股本股數"))
        records.append({"year": int(year), "month": int(month), "rows": parsed, "shares": paid_shares, "document_id": document_id})
        time.sleep(0.1)
    records.sort(key=lambda item: (item["year"], item["month"]))
    previous = None
    count = 0
    for record in records:
        parsed = record["rows"]
        cash_increase = number(find_in_rows(parsed, "現金增資")) or 0
        reduction = number(find_in_rows(parsed, "減資")) or 0
        note = find_in_rows(parsed, "其他").lstrip("> ")
        delta = record["shares"] - previous if record["shares"] is not None and previous is not None else None
        event_type = "capital_registration"
        if cash_increase > 0:
            event_type = "cash_capital_increase"
        elif reduction > 0:
            event_type = "capital_reduction"
        elif "限制員工權利新股" in note:
            event_type = "restricted_stock_change"
        elif "認股權" in note:
            event_type = "employee_option_exercise"
        elif "庫藏股" in note and delta is not None and delta < 0:
            event_type = "treasury_stock_cancellation"
        elif delta is not None and delta < 0:
            event_type = "share_cancellation"
        change_text = f"；股數變動 {delta:+,.0f} 股" if delta is not None else ""
        upsert_event(conn, code, {
            "event_key": f"capital_registration:{record['year']}-{record['month']:02d}", "event_type": event_type,
            "event_date": f"{record['year']}-{record['month']:02d}-01",
            "description": f"公司變更登記；實收股數 {record['shares']:,.0f} 股{change_text}。{note}" if record["shares"] is not None else f"公司變更登記。{note}",
            "issue_amount": cash_increase / 1_000_000 if cash_increase else (-reduction / 1_000_000 if reduction else None),
            "potential_shares": delta / 1_000_000 if delta is not None else None,
            "dilution_percent": delta / previous if delta is not None and previous else None,
            "status": "已完成變更登記", "source_url": MOPS_BASE + "t05st05",
            "source_document_id": record["document_id"],
            "quality_status": "ok" if record["shares"] is not None and previous is not None else "review",
            "quality_notes": "首筆期間缺少前期登記，無法計算股數變動" if previous is None else "",
        })
        if record["shares"] is not None:
            previous = record["shares"]
        count += 1
    return count


def ingest_treasury_from_xbrl(conn: sqlite3.Connection, code: str, years: int) -> int:
    cutoff = datetime.now().year - years
    documents = conn.execute("""
      SELECT id, report_year FROM source_documents
      WHERE stock_code=? AND quarter=4 AND report_year>=?
      ORDER BY report_year
    """, (code, cutoff)).fetchall()
    count = 0
    for document_id, year in documents:
        payment = conn.execute("""
          SELECT numeric_value FROM xbrl_facts f JOIN xbrl_contexts c
            ON c.document_id=f.document_id AND c.context_id=f.context_id
          WHERE f.document_id=? AND f.local_name='PaymentsToAcquireTreasuryShares'
            AND c.has_dimensions=0 AND c.start_date=? AND c.end_date=?
          LIMIT 1
        """, (document_id, f"{year}-01-01", f"{year}-12-31")).fetchone()
        treasury = conn.execute("""
          SELECT numeric_value FROM xbrl_facts f JOIN xbrl_contexts c
            ON c.document_id=f.document_id AND c.context_id=f.context_id
          WHERE f.document_id=? AND f.local_name='NumberOfSharesInEntityHeldByEntityAndByItsSubsidiaries'
            AND c.has_dimensions=0 AND c.instant_date=?
          LIMIT 1
        """, (document_id, f"{year}-12-31")).fetchone()
        paid = abs(payment[0]) / 1_000_000 if payment and payment[0] else 0
        held = treasury[0] / 1_000_000 if treasury and treasury[0] else 0
        if paid <= 0 and held <= 0:
            continue
        upsert_event(conn, code, {
            "event_key": f"treasury_stock:{year}", "event_type": "treasury_stock",
            "event_date": f"{year}-12-31", "description": f"XBRL 年度庫藏股：買回現金流 {paid:,.1f} 百萬元；期末持有 {held:,.1f} 百萬股",
            "issue_amount": -paid, "potential_shares": -held, "status": "年度財報已揭露",
            "source_url": MOPS_BASE + "t203sb01", "source_document_id": None, "quality_status": "ok",
            "quality_notes": "金額與股數取自年度合併 XBRL；負號表示減少外部流通股數。",
        })
        count += 1
    return count


def ingest_company(conn: sqlite3.Connection, code: str, years: int, project: Path) -> dict:
    results = {
        "restrictedStock": ingest_restricted_stock(conn, code, years, project),
        "employeeOptions": ingest_employee_options(conn, code, years, project),
        "capitalRegistrations": ingest_capital_registrations(conn, code, years, project),
        "treasuryStockYears": ingest_treasury_from_xbrl(conn, code, years),
    }
    conn.commit()
    return {"stockCode": code, **results}


def main() -> None:
    parser = argparse.ArgumentParser(description="建立增減資、員工認股權、限制員工權利新股與庫藏股事件層")
    parser.add_argument("stock_codes", nargs="+", help="股票代號，例如 2330")
    parser.add_argument("--years", type=int, default=5, help="事件回溯年數，預設 5 年")
    parser.add_argument("--db", default="data/ct_eps.sqlite")
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    conn = sqlite3.connect((project / args.db).resolve())
    conn.executescript((project / "db" / "schema.sql").read_text(encoding="utf-8"))
    try:
        results = [ingest_company(conn, code, max(1, min(args.years, 10)), project) for code in args.stock_codes]
        conn.execute("PRAGMA optimize")
        conn.commit()
    finally:
        conn.close()
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"股權事件擷取失敗：{exc}", file=sys.stderr)
        raise
