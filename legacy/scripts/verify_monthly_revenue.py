from __future__ import annotations

import json
import sqlite3
import urllib.request
from pathlib import Path


def number(value: str) -> float:
    return float(str(value).replace(",", "")) / 1000


def main() -> None:
    project = Path(__file__).resolve().parents[1]
    conn = sqlite3.connect(project / "data" / "ct_eps.sqlite")
    conn.row_factory = sqlite3.Row
    summary = conn.execute(
        """
        SELECT COUNT(*) count,
               MIN(revenue_year * 100 + revenue_month) min_period,
               MAX(revenue_year * 100 + revenue_month) max_period,
               SUM(CASE WHEN quality_status != 'ok' THEN 1 ELSE 0 END) review_count
        FROM monthly_revenues WHERE stock_code='2330'
        """
    ).fetchone()
    reconciliations = []
    for year in (2024, 2025):
        monthly = conn.execute(
            "SELECT SUM(current_month_revenue) total FROM monthly_revenues WHERE stock_code='2330' AND revenue_year=?",
            (year,),
        ).fetchone()["total"]
        december = conn.execute(
            "SELECT year_to_date_revenue FROM monthly_revenues WHERE stock_code='2330' AND revenue_year=? AND revenue_month=12",
            (year,),
        ).fetchone()["year_to_date_revenue"]
        reconciliations.append({"year": year, "monthlySumMinusDecemberYtd": round(monthly - december, 6)})
    latest = conn.execute(
        """
        SELECT current_month_revenue, year_to_date_revenue
        FROM monthly_revenues WHERE stock_code='2330'
        ORDER BY revenue_year DESC, revenue_month DESC LIMIT 1
        """
    ).fetchone()
    query_plan = [list(row) for row in conn.execute(
        "EXPLAIN QUERY PLAN SELECT * FROM monthly_revenues WHERE stock_code=? ORDER BY revenue_year DESC, revenue_month DESC LIMIT 36",
        ("2330",),
    )]
    conn.close()

    request = urllib.request.Request(
        "https://openapi.twse.com.tw/v1/opendata/t187ap05_L",
        headers={"User-Agent": "Mozilla/5.0 CT-EPS-Analytics/0.3"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        official = next(row for row in json.load(response) if row.get("公司代號") == "2330")
    official_current = number(official["營業收入-當月營收"])
    official_ytd = number(official["累計營業收入-當月累計營收"])
    raw_count = len(list((project / "data" / "raw" / "monthly-revenue" / "2330").glob("*.html")))

    result = {
        "rows": summary["count"],
        "minPeriod": summary["min_period"],
        "maxPeriod": summary["max_period"],
        "rawDocuments": raw_count,
        "reviewCount": summary["review_count"],
        "annualReconciliations": reconciliations,
        "latestOfficialCurrentMonthDifference": round(latest["current_month_revenue"] - official_current, 6),
        "latestOfficialYtdDifference": round(latest["year_to_date_revenue"] - official_ytd, 6),
        "queryPlan": query_plan,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    assert summary["count"] == 36
    assert raw_count == 36
    assert summary["review_count"] == 0
    assert all(abs(item["monthlySumMinusDecemberYtd"]) < 0.001 for item in reconciliations)
    assert abs(result["latestOfficialCurrentMonthDifference"]) < 0.001
    assert abs(result["latestOfficialYtdDifference"]) < 0.001
    assert any("idx_monthly_revenues_company_period" in row[-1] for row in query_plan)


if __name__ == "__main__":
    main()
