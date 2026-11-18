from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sqlite3
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


PARSER_VERSION = "capital-history-v1"
MOPS_REDIRECT = "https://mops.twse.com.tw/mops/api/redirectToOld"
DIVIDEND_PAGE = "https://mops.twse.com.tw/mops/#/web/t05st09_2"
USER_AGENT = "Mozilla/5.0 CT-EPS-Analytics/0.5"
BOND_ENDPOINTS = {
    "domestic_convertible_bond": "https://www.tpex.org.tw/openapi/v1/bond_ISSBD5_data",
    "overseas_convertible_bond": "https://www.tpex.org.tw/openapi/v1/bond_ISSBD6_data",
    "warrant_bond": "https://www.tpex.org.tw/openapi/v1/bond_ISSBD7_data",
}


def request_bytes(url: str, body: bytes | None = None) -> bytes:
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "zh-TW,zh;q=0.9"}
    if body is not None:
        headers.update({"Content-Type": "application/json", "Accept": "application/json"})
    req = urllib.request.Request(url, data=body, headers=headers, method="POST" if body else "GET")
    with urllib.request.urlopen(req, timeout=90) as response:
        return response.read()


def number(value: str | None) -> float | None:
    text = html.unescape(str(value or "")).strip().replace(",", "").replace("％", "")
    if not text or text in {"-", "--", "—", "N/A", "不適用"}:
        return None
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    result = float(match.group())
    return -abs(result) if negative else result


def cell_text(value: str) -> str:
    value = re.sub(r"<br\s*/?>", " ", value, flags=re.I)
    value = re.sub(r"<[^>]+>", "", value)
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def parse_date(value: str) -> str | None:
    match = re.search(r"(\d{3,4})[/.-](\d{1,2})[/.-](\d{1,2})", value)
    if not match:
        return None
    year = int(match.group(1))
    if year < 1911:
        year += 1911
    return f"{year:04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"


def fetch_dividends(code: str, start_year: int, end_year: int) -> tuple[bytes, str]:
    payload = json.dumps({
        "apiName": "ajax_t05st09_2",
        "parameters": {
            "step": 1, "firstin": "true", "off": 1, "TYPEK": "all", "isnew": "false",
            "co_id": code, "date1": str(start_year - 1911), "date2": str(end_year - 1911),
            "qryType": 2, "queryName": "co_id", "inpuType": "co_id", "encodeURIComponent": 1,
        },
    }, ensure_ascii=False).encode("utf-8")
    redirect = json.loads(request_bytes(MOPS_REDIRECT, payload))
    source_url = redirect["result"]["url"]
    return request_bytes(source_url), source_url


def parse_dividends(content: bytes) -> list[dict]:
    page = content.decode("utf-8", errors="replace")
    rows = []
    for raw_row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, flags=re.I | re.S):
        cells = [cell_text(value) for value in re.findall(r"<td[^>]*>(.*?)</td>", raw_row, flags=re.I | re.S)]
        if len(cells) < 21:
            continue
        period = cells[1]
        year_match = re.match(r"(\d{3,4})年", period)
        if not year_match:
            continue
        year = int(year_match.group(1))
        if year < 1911:
            year += 1911
        quarter_match = re.search(r"第\s*([1-4])\s*季", period)
        cash_earnings = number(cells[10])
        cash_reserve = sum(x for x in (number(cells[11]), number(cells[12])) if x is not None)
        stock_earnings = number(cells[14])
        stock_reserve = sum(x for x in (number(cells[15]), number(cells[16])) if x is not None)
        total_cash = (cash_earnings or 0) + cash_reserve
        total_stock = (stock_earnings or 0) + stock_reserve
        reported_cash_amount = number(cells[13])
        quality_notes = []
        if reported_cash_amount is not None and total_cash > 0 and number(cells[7]):
            quality_notes.append("現金總額保留原公告值；每股金額由盈餘與公積欄位加總")
        rows.append({
            "dividend_year": year,
            "period_label": period,
            "quarter": int(quarter_match.group(1)) if quarter_match else None,
            "issue_sequence": cells[3],
            "decision_status": cells[0],
            "decision_date": parse_date(cells[4]),
            "shareholder_meeting_date": parse_date(cells[5]),
            "period_net_income": number(cells[7]) / 1_000_000 if number(cells[7]) is not None else None,
            "cash_dividend_earnings": cash_earnings,
            "cash_dividend_reserves": cash_reserve,
            "total_cash_dividend": total_cash,
            "stock_dividend_earnings": stock_earnings,
            "stock_dividend_reserves": stock_reserve,
            "total_stock_dividend": total_stock,
            "total_cash_amount": reported_cash_amount / 1_000_000 if reported_cash_amount is not None else None,
            "stock_shares": number(cells[17]) / 1_000_000 if number(cells[17]) is not None else None,
            "charter_text": cells[18],
            "note": cells[19],
            "quality_status": "ok" if not quality_notes else "review",
            "quality_notes": "；".join(quality_notes),
        })
    return rows


def upsert_annual_fundamentals(conn: sqlite3.Connection, code: str) -> int:
    rows = conn.execute("""
        SELECT report_year, basic_eps, diluted_eps, parent_net_income, operating_cash_flow,
               capex, free_cash_flow, source_document_id
        FROM quarterly_financials
        WHERE stock_code=? AND basis='cumulative' AND quarter=4
        ORDER BY report_year
    """, (code,)).fetchall()
    previous_shares = None
    count = 0
    for row in rows:
        year, eps, diluted_eps, profit, cfo, capex, fcf, document_id = row
        shares = profit / eps if profit is not None and eps not in {None, 0} else None
        diluted_shares = profit / diluted_eps if profit is not None and diluted_eps not in {None, 0} else None
        growth = shares / previous_shares - 1 if shares and previous_shares else None
        notes = []
        if shares is None:
            notes.append("無法以歸母淨利除以 EPS 推導加權平均股數")
        if fcf is None:
            notes.append("缺少營業現金流或資本支出")
        conn.execute("""
            INSERT INTO annual_fundamentals
            (stock_code, fiscal_year, basic_eps, diluted_eps, parent_net_income, operating_cash_flow,
             capex, free_cash_flow, implied_average_shares, implied_diluted_shares, share_growth_rate,
             source_document_id, quality_status, quality_notes, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(stock_code, fiscal_year) DO UPDATE SET
              basic_eps=excluded.basic_eps, diluted_eps=excluded.diluted_eps,
              parent_net_income=excluded.parent_net_income, operating_cash_flow=excluded.operating_cash_flow,
              capex=excluded.capex, free_cash_flow=excluded.free_cash_flow,
              implied_average_shares=excluded.implied_average_shares,
              implied_diluted_shares=excluded.implied_diluted_shares, share_growth_rate=excluded.share_growth_rate,
              source_document_id=excluded.source_document_id, quality_status=excluded.quality_status,
              quality_notes=excluded.quality_notes, updated_at=excluded.updated_at
        """, (code, year, eps, diluted_eps, profit, cfo, capex, fcf, shares, diluted_shares, growth,
              document_id, "ok" if not notes else "review", "；".join(notes), datetime.now(timezone.utc).isoformat()))
        if shares:
            previous_shares = shares
        count += 1
    return count


def save_document(conn: sqlite3.Connection, table: str, code: str, source_type: str, source_url: str,
                  content: bytes, raw_path: Path, relative_root: Path, start_year: int | None = None,
                  end_year: int | None = None) -> int:
    digest = hashlib.sha256(content).hexdigest()
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(content)
    now = datetime.now(timezone.utc).isoformat()
    if table == "dividend_documents":
        conn.execute("""
          INSERT OR IGNORE INTO dividend_documents
          (stock_code,start_year,end_year,source_url,fetched_at,sha256,raw_path,parser_version)
          VALUES (?,?,?,?,?,?,?,?)
        """, (code, start_year, end_year, DIVIDEND_PAGE, now, digest,
              str(raw_path.relative_to(relative_root)), PARSER_VERSION))
        return conn.execute("""
          SELECT id FROM dividend_documents
          WHERE stock_code=? AND start_year=? AND end_year=? AND sha256=?
        """, (code, start_year, end_year, digest)).fetchone()[0]
    conn.execute("""
      INSERT OR IGNORE INTO dilution_documents
      (stock_code,source_type,source_url,fetched_at,sha256,raw_path,parser_version)
      VALUES (?,?,?,?,?,?,?)
    """, (code, source_type, source_url, now, digest, str(raw_path.relative_to(relative_root)), PARSER_VERSION))
    return conn.execute("""
      SELECT id FROM dilution_documents WHERE stock_code=? AND source_type=? AND sha256=?
    """, (code, source_type, digest)).fetchone()[0]


def ingest_bonds(conn: sqlite3.Connection, code: str, raw_root: Path, project: Path) -> int:
    count = 0
    for event_type, source_url in BOND_ENDPOINTS.items():
        content = request_bytes(source_url)
        raw_path = raw_root / code / f"{event_type}.json"
        document_id = save_document(conn, "dilution_documents", code, event_type, source_url, content, raw_path, project)
        records = json.loads(content)
        for item in records:
            if str(item.get("IssuerCode", "")).strip() != code:
                continue
            currency = str(item.get("Currency", "")).strip()
            outstanding = number(item.get("OutstandingAmount"))
            conversion_price = number(item.get("Conversion/ExchangePriceAtIssuance"))
            potential_shares = None
            notes = []
            if currency in {"TWD", "NTD", "新臺幣"} and outstanding is not None and conversion_price:
                potential_shares = outstanding / conversion_price
            elif outstanding and conversion_price:
                notes.append("非新臺幣債券，未在缺少匯率時換算潛在股數")
            event_key = f"{event_type}:{item.get('BondCode', '')}"
            conn.execute("""
              INSERT INTO dilution_events
              (stock_code,event_key,event_type,event_date,effective_date,description,currency,issue_amount,
               outstanding_amount,conversion_price,potential_shares,dilution_percent,status,source_url,
               source_document_id,quality_status,quality_notes,updated_at)
              VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
              ON CONFLICT(stock_code,event_key) DO UPDATE SET
               event_date=excluded.event_date,effective_date=excluded.effective_date,description=excluded.description,
               currency=excluded.currency,issue_amount=excluded.issue_amount,outstanding_amount=excluded.outstanding_amount,
               conversion_price=excluded.conversion_price,potential_shares=excluded.potential_shares,
               status=excluded.status,source_document_id=excluded.source_document_id,
               quality_status=excluded.quality_status,quality_notes=excluded.quality_notes,updated_at=excluded.updated_at
            """, (code, event_key, event_type, parse_date(str(item.get("IssueDate", ""))),
                  parse_date(str(item.get("MaturityDate", ""))),
                  f"{item.get('ShortName', '')}（{item.get('BondCode', '')}）", currency,
                  number(item.get("IssueAmount")), outstanding, conversion_price, potential_shares, None,
                  str(item.get("ListingStatus", "")), source_url, document_id,
                  "ok" if not notes else "review", "；".join(notes), datetime.now(timezone.utc).isoformat()))
            count += 1
    return count


def derive_share_events(conn: sqlite3.Connection, code: str) -> int:
    annual = conn.execute("""
      SELECT fiscal_year, implied_average_shares, share_growth_rate
      FROM annual_fundamentals WHERE stock_code=? ORDER BY fiscal_year
    """, (code,)).fetchall()
    count = 0
    for year, shares, growth in annual:
        if growth is None or abs(growth) < 0.002:
            continue
        event_key = f"share_change:{year}"
        description = "加權平均股數增加，可能來自現金增資、可轉債、員工獎酬或股票股利，需覆核事件性質" if growth > 0 else "加權平均股數減少，可能來自庫藏股或減資，需覆核事件性質"
        conn.execute("""
          INSERT INTO dilution_events
          (stock_code,event_key,event_type,event_date,effective_date,description,currency,issue_amount,
           outstanding_amount,conversion_price,potential_shares,dilution_percent,status,source_url,
           source_document_id,quality_status,quality_notes,updated_at)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(stock_code,event_key) DO UPDATE SET
           description=excluded.description,potential_shares=excluded.potential_shares,
           dilution_percent=excluded.dilution_percent,updated_at=excluded.updated_at
        """, (code, event_key, "weighted_share_change", f"{year}-12-31", None, description, "TWD",
              None, None, None, shares, growth, "需覆核", DIVIDEND_PAGE, None, "review",
              "此項由年度歸母淨利／基本 EPS 的隱含加權平均股數推導，不能單獨判定為現金增資",
              datetime.now(timezone.utc).isoformat()))
        count += 1
    return count


def ingest_company(conn: sqlite3.Connection, code: str, years: int, project: Path) -> dict:
    current_year = datetime.now().year
    # 保留指定年數的完整歷史年度，另加本年度已公告資料。
    start_year = current_year - years
    content, source_url = fetch_dividends(code, start_year, current_year)
    dividend_rows = parse_dividends(content)
    if not dividend_rows:
        raise RuntimeError(f"MOPS 查無 {code} 的股利歷史")
    raw_path = project / "data" / "raw" / "dividends" / code / f"{code}-{start_year}-{current_year}.html"
    document_id = save_document(conn, "dividend_documents", code, "dividends", source_url, content, raw_path,
                                project, start_year, current_year)
    conn.execute("DELETE FROM dividend_declarations WHERE stock_code=? AND dividend_year BETWEEN ? AND ?", (code, start_year, current_year))
    now = datetime.now(timezone.utc).isoformat()
    for row in dividend_rows:
        conn.execute("""
          INSERT INTO dividend_declarations
          (stock_code,dividend_year,period_label,quarter,issue_sequence,decision_status,decision_date,
           shareholder_meeting_date,period_net_income,cash_dividend_earnings,cash_dividend_reserves,
           total_cash_dividend,stock_dividend_earnings,stock_dividend_reserves,total_stock_dividend,
           total_cash_amount,stock_shares,charter_text,note,source_document_id,quality_status,quality_notes,updated_at)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (code, row["dividend_year"], row["period_label"], row["quarter"], row["issue_sequence"],
              row["decision_status"], row["decision_date"], row["shareholder_meeting_date"],
              row["period_net_income"], row["cash_dividend_earnings"], row["cash_dividend_reserves"],
              row["total_cash_dividend"], row["stock_dividend_earnings"], row["stock_dividend_reserves"],
              row["total_stock_dividend"], row["total_cash_amount"], row["stock_shares"],
              row["charter_text"], row["note"], document_id, row["quality_status"], row["quality_notes"], now))
    annual_count = upsert_annual_fundamentals(conn, code)
    bond_count = ingest_bonds(conn, code, project / "data" / "raw" / "dilution", project)
    share_event_count = derive_share_events(conn, code)
    conn.commit()
    return {"stockCode": code, "dividendRows": len(dividend_rows), "annualRows": annual_count,
            "bondEvents": bond_count, "derivedShareEvents": share_event_count}


def main() -> None:
    parser = argparse.ArgumentParser(description="建立 3～5 年股利、自由現金流與潛在稀釋資料層")
    parser.add_argument("stock_codes", nargs="+", help="股票代號，例如 2330 6488")
    parser.add_argument("--years", type=int, default=5, help="股利歷史年數，預設 5 年")
    parser.add_argument("--db", default="data/ct_eps.sqlite")
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    db_path = (project / args.db).resolve()
    conn = sqlite3.connect(db_path)
    conn.executescript((project / "db" / "schema.sql").read_text(encoding="utf-8"))
    try:
        results = [ingest_company(conn, code, max(3, min(args.years, 5)), project) for code in args.stock_codes]
        conn.execute("PRAGMA optimize")
        conn.commit()
    finally:
        conn.close()
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"資本配置歷史擷取失敗：{exc}", file=sys.stderr)
        raise
