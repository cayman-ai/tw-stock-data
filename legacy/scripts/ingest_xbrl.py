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
import xml.etree.ElementTree as ET
from calendar import monthrange
from datetime import date, datetime, timezone
from pathlib import Path


PARSER_VERSION = "xbrl-v1"
MOPS_REDIRECT = "https://mops.twse.com.tw/mops/api/redirectToOld"
MOPS_DOWNLOAD_HOST = "https://mopsov.twse.com.tw"
USER_AGENT = "Mozilla/5.0 CT-EPS-Analytics/0.2"

ALIASES = {
    "revenue": ["Revenue", "OperatingRevenue"],
    "gross_profit": ["GrossProfitLossFromOperations", "GrossProfit"],
    "operating_expense": ["OperatingExpenses", "OperatingExpense"],
    "operating_profit": ["ProfitLossFromOperatingActivities", "ProfitLossFromOperations"],
    "non_operating": ["NonoperatingIncomeAndExpenses"],
    "pretax_income": ["ProfitLossBeforeTax"],
    "income_tax": ["IncomeTaxExpenseContinuingOperations", "IncomeTaxExpense"],
    "net_income": ["ProfitLoss"],
    "parent_net_income": ["ProfitLossAttributableToOwnersOfParent"],
    "nci": ["ProfitLossAttributableToNoncontrollingInterests"],
    "basic_eps": ["BasicEarningsLossPerShare"],
    "diluted_eps": ["DilutedEarningsLossPerShare"],
    "parent_equity": ["EquityAttributableToOwnersOfParent"],
    "accounts_receivable": ["AccountsReceivableNet", "TradeReceivables"],
    "inventories": ["Inventories"],
    "accounts_payable": ["AccountsPayable", "TradePayables"],
    "operating_cash_flow": ["CashFlowsFromUsedInOperatingActivities", "NetCashFlowsFromUsedInOperatingActivities"],
    "capex": [
        "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
        "PaymentsToAcquirePropertyPlantAndEquipment",
    ],
}

FLOW_FIELDS = {
    "revenue", "gross_profit", "operating_expense", "operating_profit", "non_operating",
    "pretax_income", "income_tax", "net_income", "parent_net_income", "nci",
    "basic_eps", "diluted_eps", "operating_cash_flow", "capex", "free_cash_flow",
}


def http_request(url: str, *, body: bytes | None = None, content_type: str | None = None) -> bytes:
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "zh-TW,zh;q=0.9"}
    if content_type:
        headers["Content-Type"] = content_type
        headers["Accept"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method="POST" if body else "GET")
    with urllib.request.urlopen(request, timeout=90) as response:
        return response.read()


def get_document_list(stock_code: str) -> list[dict]:
    payload = json.dumps(
        {
            "apiName": "ajax_t203sb01",
            "parameters": {
                "co_id": stock_code,
                "TYPEK": "all",
                "encodeURIComponent": 1,
                "step": 1,
                "firstin": 1,
                "off": 1,
            },
        }
    ).encode("utf-8")
    response = json.loads(http_request(MOPS_REDIRECT, body=payload, content_type="application/json"))
    listing_url = response["result"]["url"]
    listing = http_request(listing_url).decode("utf-8", errors="replace")
    pattern = re.compile(
        r"<td>(\d{3})Q([1-4])</td><td>([^<]+)</td>.*?"
        r"window\.open\('(/server-java/FileDownLoad\?[^']+)'",
        re.DOTALL,
    )
    documents = []
    for roc_year, quarter, report_label, path in pattern.findall(listing):
        params = html.unescape(path)
        report_id_match = re.search(r"report_id=([^&]+)", params)
        report_id = report_id_match.group(1) if report_id_match else ""
        documents.append(
            {
                "report_year": int(roc_year) + 1911,
                "quarter": int(quarter),
                "report_label": report_label.strip(),
                "report_id": report_id,
                "source_url": MOPS_DOWNLOAD_HOST + params,
            }
        )
    preferred = {}
    for item in documents:
        key = (item["report_year"], item["quarter"])
        if key not in preferred or item["report_id"] == "C":
            preferred[key] = item
    return sorted(preferred.values(), key=lambda item: (item["report_year"], item["quarter"]), reverse=True)


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].split(":")[-1]


def parse_number(element: ET.Element) -> float | None:
    nil = element.attrib.get("{http://www.w3.org/2001/XMLSchema-instance}nil")
    if nil == "true":
        return None
    text = "".join(element.itertext()).strip().replace(",", "").replace(" ", "")
    if not text or text in {"-", "—", "N/A"}:
        return None
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    try:
        value = float(text)
    except ValueError:
        return None
    scale = int(element.attrib.get("scale", "0") or 0)
    value *= 10 ** scale
    if element.attrib.get("sign") == "-" or negative:
        value *= -1
    return value


def parse_xbrl(content: bytes) -> tuple[dict[str, dict], list[dict]]:
    root = ET.fromstring(content)
    contexts: dict[str, dict] = {}
    for element in root.iter():
        if local_name(element.tag) != "context":
            continue
        context_id = element.attrib.get("id")
        if not context_id:
            continue
        item = {"start_date": None, "end_date": None, "instant_date": None, "has_dimensions": 0}
        for child in element.iter():
            child_name = local_name(child.tag)
            if child_name in {"startDate", "endDate", "instant"}:
                item[{"startDate": "start_date", "endDate": "end_date", "instant": "instant_date"}[child_name]] = (child.text or "").strip()
            if child_name in {"explicitMember", "typedMember"}:
                item["has_dimensions"] = 1
        contexts[context_id] = item

    facts = []
    for element in root.iter():
        if local_name(element.tag) not in {"nonFraction", "nonNumeric"}:
            continue
        concept = element.attrib.get("name")
        context_id = element.attrib.get("contextRef")
        if not concept or not context_id or context_id not in contexts:
            continue
        value = parse_number(element) if local_name(element.tag) == "nonFraction" else None
        facts.append(
            {
                "concept": concept,
                "local_name": concept.split(":")[-1],
                "context_id": context_id,
                "unit_ref": element.attrib.get("unitRef"),
                "decimals_text": element.attrib.get("decimals"),
                "scale_value": int(element.attrib.get("scale", "0") or 0),
                "language": element.attrib.get("{http://www.w3.org/XML/1998/namespace}lang"),
                "numeric_value": value,
                "text_value": None if value is not None else "".join(element.itertext()).strip()[:4000],
            }
        )
    return contexts, facts


def quarter_end(year: int, quarter: int) -> date:
    month = quarter * 3
    return date(year, month, monthrange(year, month)[1])


def select_fact(facts: list[dict], contexts: dict[str, dict], aliases: list[str], year: int, quarter: int, instant: bool) -> float | None:
    target_end = quarter_end(year, quarter)
    candidates = []
    for fact in facts:
        if fact["local_name"] not in aliases or fact["numeric_value"] is None:
            continue
        context = contexts[fact["context_id"]]
        if context["has_dimensions"]:
            continue
        try:
            if instant:
                if not context["instant_date"]:
                    continue
                end = date.fromisoformat(context["instant_date"])
                if end != target_end:
                    continue
                duration = 0
            else:
                if not context["start_date"] or not context["end_date"]:
                    continue
                start = date.fromisoformat(context["start_date"])
                end = date.fromisoformat(context["end_date"])
                if end != target_end or start.year != year:
                    continue
                duration = (end - start).days
        except ValueError:
            continue
        alias_priority = aliases.index(fact["local_name"])
        candidates.append((alias_priority, -duration, fact["numeric_value"]))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0][2]


def normalized_record(facts: list[dict], contexts: dict[str, dict], year: int, quarter: int) -> dict:
    instant_fields = {"parent_equity", "accounts_receivable", "inventories", "accounts_payable"}
    record = {}
    for field, aliases in ALIASES.items():
        value = select_fact(facts, contexts, aliases, year, quarter, field in instant_fields)
        if value is not None and field not in {"basic_eps", "diluted_eps"}:
            value /= 1_000_000
        record[field] = value
    if record["operating_cash_flow"] is not None and record["capex"] is not None:
        record["free_cash_flow"] = record["operating_cash_flow"] - abs(record["capex"])
    else:
        record["free_cash_flow"] = None
    return record


def difference_record(current: dict, previous: dict | None, quarter: int) -> dict:
    result = {}
    for key, value in current.items():
        if key not in FLOW_FIELDS or quarter == 1 or previous is None:
            result[key] = value
        else:
            prior = previous.get(key)
            result[key] = value - prior if value is not None and prior is not None else None
    return result


def quality(record: dict) -> tuple[str, str]:
    required = ["revenue", "operating_profit", "pretax_income", "parent_net_income", "parent_equity"]
    missing = [key for key in required if record.get(key) is None]
    checks = []
    if record.get("gross_profit") is not None and record.get("operating_expense") is not None and record.get("operating_profit") is not None:
        diff = record["gross_profit"] - record["operating_expense"] - record["operating_profit"]
        tolerance = max(1.0, abs(record["operating_profit"]) * 0.01)
        if abs(diff) > tolerance:
            checks.append(f"毛利－營業費用與營業利益差異 {diff:.1f} 百萬元")
    notes = []
    if missing:
        notes.append("缺少欄位：" + "、".join(missing))
    notes.extend(checks)
    return ("ok" if not notes else "review", "；".join(notes))


def upsert_financial(conn: sqlite3.Connection, code: str, year: int, quarter: int, basis: str, record: dict, document_id: int) -> None:
    status, notes = quality(record)
    fields = list(ALIASES) + ["free_cash_flow"]
    columns = ", ".join(fields)
    placeholders = ", ".join("?" for _ in fields)
    updates = ", ".join(f"{field}=excluded.{field}" for field in fields)
    conn.execute(
        f"""
        INSERT INTO quarterly_financials (
          stock_code, report_year, quarter, basis, {columns}, source_document_id,
          quality_status, quality_notes, updated_at
        ) VALUES (?, ?, ?, ?, {placeholders}, ?, ?, ?, ?)
        ON CONFLICT(stock_code, report_year, quarter, basis) DO UPDATE SET
          {updates}, source_document_id=excluded.source_document_id,
          quality_status=excluded.quality_status, quality_notes=excluded.quality_notes,
          updated_at=excluded.updated_at
        """,
        [code, year, quarter, basis, *[record.get(field) for field in fields], document_id, status, notes, datetime.now(timezone.utc).isoformat()],
    )


def ingest_company(code: str, periods: int, db_path: Path, raw_dir: Path, schema_path: Path) -> dict:
    documents = get_document_list(code)[:periods]
    if not documents:
        raise RuntimeError(f"MOPS 查無 {code} 的 XBRL 合併財報")
    raw_company = raw_dir / code
    raw_company.mkdir(parents=True, exist_ok=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(schema_path.read_text(encoding="utf-8"))
    parsed_by_period = {}
    ingested = []
    try:
        for index, document in enumerate(sorted(documents, key=lambda item: (item["report_year"], item["quarter"]))):
            last_error = None
            for attempt in range(3):
                try:
                    content = http_request(document["source_url"])
                    contexts, facts = parse_xbrl(content)
                    break
                except (ET.ParseError, OSError, ValueError) as exc:
                    last_error = exc
                    if attempt < 2:
                        time.sleep(0.8 * (attempt + 1))
            else:
                raise RuntimeError(
                    f"{code} {document['report_year']}Q{document['quarter']} XBRL 下載或解析失敗：{last_error}"
                )
            digest = hashlib.sha256(content).hexdigest()
            raw_path = raw_company / f"{code}-{document['report_year']}Q{document['quarter']}-C.xhtml"
            raw_path.write_bytes(content)
            conn.execute(
                "DELETE FROM quarterly_financials WHERE stock_code=? AND report_year=? AND quarter=?",
                (code, document["report_year"], document["quarter"]),
            )
            conn.execute(
                """DELETE FROM annual_fundamentals
                   WHERE source_document_id IN (
                     SELECT id FROM source_documents
                     WHERE stock_code=? AND report_year=? AND quarter=? AND report_type=?
                   )""",
                (code, document["report_year"], document["quarter"], "C"),
            )
            conn.execute(
                "DELETE FROM source_documents WHERE stock_code=? AND report_year=? AND quarter=? AND report_type=?",
                (code, document["report_year"], document["quarter"], "C"),
            )
            cursor = conn.execute(
                """
                INSERT INTO source_documents
                (stock_code, report_year, quarter, report_type, source_url, fetched_at, sha256, raw_path, parser_version)
                VALUES (?, ?, ?, 'C', ?, ?, ?, ?, ?)
                """,
                (
                    code, document["report_year"], document["quarter"], document["source_url"],
                    datetime.now(timezone.utc).isoformat(), digest, str(raw_path.relative_to(db_path.parent.parent)), PARSER_VERSION,
                ),
            )
            document_id = cursor.lastrowid
            conn.executemany(
                """
                INSERT INTO xbrl_contexts
                (document_id, context_id, start_date, end_date, instant_date, has_dimensions)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (document_id, context_id, item["start_date"], item["end_date"], item["instant_date"], item["has_dimensions"])
                    for context_id, item in contexts.items()
                ],
            )
            conn.executemany(
                """
                INSERT INTO xbrl_facts
                (document_id, concept, local_name, context_id, unit_ref, decimals_text, scale_value, language, numeric_value, text_value)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        document_id, fact["concept"], fact["local_name"], fact["context_id"], fact["unit_ref"],
                        fact["decimals_text"], fact["scale_value"], fact["language"], fact["numeric_value"], fact["text_value"],
                    )
                    for fact in facts
                ],
            )
            cumulative = normalized_record(facts, contexts, document["report_year"], document["quarter"])
            parsed_by_period[(document["report_year"], document["quarter"])] = (cumulative, document_id)
            upsert_financial(conn, code, document["report_year"], document["quarter"], "cumulative", cumulative, document_id)
            ingested.append({"year": document["report_year"], "quarter": document["quarter"], "facts": len(facts), "sha256": digest})
            conn.commit()
            if index + 1 < len(documents):
                time.sleep(0.15)

        for (year, quarter), (current, document_id) in sorted(parsed_by_period.items()):
            previous = parsed_by_period.get((year, quarter - 1), (None, None))[0]
            single = difference_record(current, previous, quarter)
            upsert_financial(conn, code, year, quarter, "single", single, document_id)
        conn.commit()
        conn.execute("PRAGMA optimize")
        conn.commit()
    finally:
        conn.close()
    return {"stockCode": code, "periods": ingested, "database": str(db_path), "rawDirectory": str(raw_company)}


def main() -> None:
    parser = argparse.ArgumentParser(description="擷取並解析 MOPS Inline XBRL 歷史財報")
    parser.add_argument("stock_codes", nargs="+", help="股票代號，例如 2330 6488")
    parser.add_argument("--periods", type=int, default=12, help="每家公司擷取季數，預設 12")
    parser.add_argument("--db", default="data/ct_eps.sqlite")
    parser.add_argument("--raw-dir", default="data/raw/xbrl")
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    db_path = (project / args.db).resolve()
    raw_dir = (project / args.raw_dir).resolve()
    schema_path = project / "db" / "schema.sql"
    results = []
    for code in args.stock_codes:
        if not re.fullmatch(r"[0-9A-Za-z]{4,6}", code):
            raise SystemExit(f"無效股票代號：{code}")
        results.append(ingest_company(code, max(1, min(args.periods, 20)), db_path, raw_dir, schema_path))
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"XBRL 擷取失敗：{exc}", file=sys.stderr)
        raise
