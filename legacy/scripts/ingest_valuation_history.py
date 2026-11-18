from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path


PARSER_VERSION = "valuation-history-v1"
USER_AGENT = "Mozilla/5.0 CT-EPS-Analytics/0.28"


def subtract_months(year: int, month: int, offset: int) -> tuple[int, int]:
    serial = year * 12 + month - 1 - offset
    return serial // 12, serial % 12 + 1


def request_json(url: str) -> tuple[bytes, dict]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                content = response.read()
            return content, json.loads(content)
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(attempt + 1)
    raise RuntimeError(f"交易所資料連線失敗：{last_error}")


def source_url(market: str, code: str, year: int, month: int) -> str:
    if market == "twse":
        query = urllib.parse.urlencode({"response": "json", "date": f"{year}{month:02d}01", "stockNo": code})
        return f"https://www.twse.com.tw/exchangeReport/BWIBBU?{query}"
    query = urllib.parse.urlencode({"date": f"{year}/{month:02d}/01", "code": code, "response": "json"})
    return f"https://www.tpex.org.tw/www/zh-tw/afterTrading/peQryStock?{query}"


def rows_from(payload: dict, market: str) -> list[list]:
    if market == "twse":
        return payload.get("data") or [] if payload.get("stat") == "OK" else []
    tables = payload.get("tables") or []
    return tables[0].get("data") or [] if tables else []


def parse_trade_date(value: object) -> str | None:
    text = str(value or "").strip()
    match = re.search(r"(\d{3})[年/](\d{2})[月/](\d{2})", text)
    if not match:
        return None
    return f"{int(match.group(1)) + 1911:04d}-{match.group(2)}-{match.group(3)}"


def parse_pe(value: object) -> float | None:
    text = str(value or "").replace(",", "").strip()
    try:
        number = float(text)
        return number if number > 0 else None
    except ValueError:
        return None


def detect_market(code: str, year: int, month: int) -> str:
    for market in ("twse", "tpex"):
        _, payload = request_json(source_url(market, code, year, month))
        if rows_from(payload, market):
            return market
    raise RuntimeError(f"找不到 {code} 的上市或上櫃歷史本益比資料")


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
      CREATE TABLE IF NOT EXISTS valuation_documents(
        id INTEGER PRIMARY KEY, stock_code TEXT NOT NULL, market TEXT NOT NULL, year_month TEXT NOT NULL,
        source_url TEXT NOT NULL, fetched_at TEXT NOT NULL, sha256 TEXT NOT NULL, raw_path TEXT NOT NULL,
        parser_version TEXT NOT NULL, UNIQUE(stock_code,market,year_month,sha256)
      );
      CREATE TABLE IF NOT EXISTS daily_valuations(
        id INTEGER PRIMARY KEY, stock_code TEXT NOT NULL, market TEXT NOT NULL, trade_date TEXT NOT NULL,
        pe_ratio REAL, raw_pe TEXT, financial_period TEXT, source_document_id INTEGER NOT NULL,
        quality_status TEXT NOT NULL, quality_notes TEXT, updated_at TEXT NOT NULL,
        UNIQUE(stock_code,trade_date)
      );
      CREATE INDEX IF NOT EXISTS idx_daily_valuations_code_date ON daily_valuations(stock_code,trade_date);
    """)


def ingest(code: str, months: int, db_path: Path, raw_root: Path, delay: float) -> dict:
    today = date.today()
    end_year, end_month = subtract_months(today.year, today.month, 1)
    market = detect_market(code, end_year, end_month)
    conn = sqlite3.connect(db_path)
    ensure_schema(conn)
    total_rows = valid_rows = 0
    try:
        for offset in reversed(range(months)):
            year, month = subtract_months(end_year, end_month, offset)
            url = source_url(market, code, year, month)
            content, payload = request_json(url)
            rows = rows_from(payload, market)
            raw_path = raw_root / code / market / f"{year}{month:02d}.json"
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_bytes(content)
            fetched_at = datetime.now(timezone.utc).isoformat()
            sha256 = hashlib.sha256(content).hexdigest()
            cursor = conn.execute("""
              INSERT OR IGNORE INTO valuation_documents
              (stock_code,market,year_month,source_url,fetched_at,sha256,raw_path,parser_version)
              VALUES(?,?,?,?,?,?,?,?)
            """, (code, market, f"{year}{month:02d}", url, fetched_at, sha256, str(raw_path), PARSER_VERSION))
            document_id = cursor.lastrowid or conn.execute("""
              SELECT id FROM valuation_documents WHERE stock_code=? AND market=? AND year_month=? AND sha256=?
            """, (code, market, f"{year}{month:02d}", sha256)).fetchone()[0]
            conn.execute("DELETE FROM daily_valuations WHERE stock_code=? AND trade_date LIKE ?", (code, f"{year}-{month:02d}-%"))
            for row in rows:
                trade_date = parse_trade_date(row[0] if row else None)
                if not trade_date:
                    continue
                pe_index = 3 if market == "twse" else 1
                raw_pe = str(row[pe_index] if len(row) > pe_index else "").strip()
                pe_ratio = parse_pe(raw_pe)
                financial_period = str(row[5] if len(row) > 5 else "").strip()
                status = "ok" if pe_ratio is not None else "excluded"
                note = None if pe_ratio is not None else "本益比缺值、非正數或近四季每股參考稅後純益不為正"
                conn.execute("""
                  INSERT INTO daily_valuations
                  (stock_code,market,trade_date,pe_ratio,raw_pe,financial_period,source_document_id,quality_status,quality_notes,updated_at)
                  VALUES(?,?,?,?,?,?,?,?,?,?)
                """, (code, market, trade_date, pe_ratio, raw_pe, financial_period, document_id, status, note, fetched_at))
                total_rows += 1
                valid_rows += pe_ratio is not None
            conn.commit()
            if delay and offset:
                time.sleep(delay)
    finally:
        conn.close()
    return {"stockCode": code, "market": market, "months": months, "rows": total_rows, "validRows": valid_rows}


def main() -> None:
    parser = argparse.ArgumentParser(description="建立近三年個股官方每日有效本益比歷史")
    parser.add_argument("stock_code")
    parser.add_argument("--months", type=int, default=36)
    parser.add_argument("--db", default="data/ct_eps.sqlite")
    parser.add_argument("--raw-dir", default="data/raw/valuation")
    parser.add_argument("--delay", type=float, default=0.15)
    args = parser.parse_args()
    code = args.stock_code.strip()
    if not code.isalnum() or not 4 <= len(code) <= 6:
        raise SystemExit("股票代號格式錯誤")
    project = Path(__file__).resolve().parents[1]
    result = ingest(code, max(1, min(args.months, 60)), project / args.db, project / args.raw_dir, max(0, args.delay))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
