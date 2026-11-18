from __future__ import annotations

import json
import sqlite3
import urllib.request
from pathlib import Path


SAMPLES = {
    "2308": {
        "name": "台達電",
        "market": "上市",
        "industry": "電子零組件業",
        "endpoint": "https://openapi.twse.com.tw/v1/opendata/t187ap05_L",
    },
    "6488": {
        "name": "環球晶",
        "market": "上櫃",
        "industry": "半導體業",
        "endpoint": "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O",
    },
    "5904": {
        "name": "寶雅*",
        "market": "上櫃",
        "industry": "居家生活",
        "endpoint": "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O",
    },
}


def number(value: str) -> float:
    return float(str(value).replace(",", "")) / 1000


def code_of(row: dict) -> str:
    return str(row.get("公司代號") or row.get("SecuritiesCompanyCode") or row.get("Code") or "").strip()


def official_rows(endpoint: str, cache: dict[str, list[dict]]) -> list[dict]:
    if endpoint not in cache:
        request = urllib.request.Request(endpoint, headers={"User-Agent": "Mozilla/5.0 CT-EPS-Analytics/0.3"})
        with urllib.request.urlopen(request, timeout=60) as response:
            cache[endpoint] = json.load(response)
    return cache[endpoint]


def main() -> None:
    project = Path(__file__).resolve().parents[1]
    conn = sqlite3.connect(project / "data" / "ct_eps.sqlite")
    conn.row_factory = sqlite3.Row
    cache: dict[str, list[dict]] = {}
    results = []
    for code, metadata in SAMPLES.items():
        rows = conn.execute(
            """
            SELECT * FROM monthly_revenues WHERE stock_code=?
            ORDER BY revenue_year, revenue_month
            """,
            (code,),
        ).fetchall()
        periods = [row["revenue_year"] * 12 + row["revenue_month"] - 1 for row in rows]
        continuous = all(current - previous == 1 for previous, current in zip(periods, periods[1:]))
        reconciliations = []
        for year in (2024, 2025):
            year_rows = [row for row in rows if row["revenue_year"] == year]
            december = next((row for row in year_rows if row["revenue_month"] == 12), None)
            difference = sum(row["current_month_revenue"] for row in year_rows) - december["year_to_date_revenue"]
            reconciliations.append({"year": year, "difference": round(difference, 6)})
        latest = rows[-1]
        official = next(row for row in official_rows(metadata["endpoint"], cache) if code_of(row) == code)
        current_difference = latest["current_month_revenue"] - number(official["營業收入-當月營收"])
        ytd_difference = latest["year_to_date_revenue"] - number(official["累計營業收入-當月累計營收"])
        raw_count = len(list((project / "data" / "raw" / "monthly-revenue" / code).glob("*.html")))
        result = {
            "stockCode": code,
            **metadata,
            "months": len(rows),
            "startPeriod": f"{rows[0]['revenue_year']}-{rows[0]['revenue_month']:02d}",
            "endPeriod": f"{latest['revenue_year']}-{latest['revenue_month']:02d}",
            "rawDocuments": raw_count,
            "continuous": continuous,
            "reviewCount": sum(row["quality_status"] != "ok" for row in rows),
            "annualReconciliations": reconciliations,
            "latestCurrentMonthDifference": round(current_difference, 6),
            "latestYtdDifference": round(ytd_difference, 6),
        }
        results.append(result)
        assert result["months"] == 36
        assert result["rawDocuments"] == 36
        assert result["continuous"]
        assert result["reviewCount"] == 0
        assert all(abs(item["difference"]) < 0.001 for item in reconciliations)
        assert abs(result["latestCurrentMonthDifference"]) < 0.001
        assert abs(result["latestYtdDifference"]) < 0.001
    conn.close()
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
