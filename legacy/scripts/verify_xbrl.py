from __future__ import annotations

import json
import sqlite3
import urllib.request
from pathlib import Path


def main() -> None:
    project = Path(__file__).resolve().parents[1]
    conn = sqlite3.connect(project / "data" / "ct_eps.sqlite")
    conn.row_factory = sqlite3.Row
    documents = conn.execute(
        "SELECT COUNT(*) count, MIN(report_year * 10 + quarter) min_period, MAX(report_year * 10 + quarter) max_period FROM source_documents WHERE stock_code='2330'"
    ).fetchone()
    facts = conn.execute("SELECT COUNT(*) count FROM xbrl_facts").fetchone()["count"]
    reconciliations = []
    for year in (2024, 2025):
        annual = conn.execute(
            "SELECT revenue, parent_net_income, operating_cash_flow FROM quarterly_financials WHERE stock_code='2330' AND report_year=? AND quarter=4 AND basis='cumulative'",
            (year,),
        ).fetchone()
        singles = conn.execute(
            "SELECT SUM(revenue) revenue, SUM(parent_net_income) parent_net_income, SUM(operating_cash_flow) operating_cash_flow FROM quarterly_financials WHERE stock_code='2330' AND report_year=? AND basis='single'",
            (year,),
        ).fetchone()
        reconciliations.append(
            {
                "year": year,
                "revenueDifference": round(singles["revenue"] - annual["revenue"], 6),
                "parentNetIncomeDifference": round(singles["parent_net_income"] - annual["parent_net_income"], 6),
                "operatingCashFlowDifference": round(singles["operating_cash_flow"] - annual["operating_cash_flow"], 6),
            }
        )
    query_plan = [list(row) for row in conn.execute(
        "EXPLAIN QUERY PLAN SELECT * FROM quarterly_financials WHERE stock_code=? AND basis=? ORDER BY report_year DESC, quarter DESC",
        ("2330", "single"),
    )]
    latest = conn.execute(
        "SELECT report_year, quarter, parent_net_income FROM quarterly_financials WHERE stock_code='2330' AND basis='cumulative' ORDER BY report_year DESC, quarter DESC LIMIT 1"
    ).fetchone()
    conn.close()

    request = urllib.request.Request(
        "https://openapi.twse.com.tw/v1/opendata/t187ap06_L_ci",
        headers={"User-Agent": "Mozilla/5.0 CT-EPS-Analytics/0.2"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        official = next(row for row in json.load(response) if row.get("公司代號") == "2330")
    official_parent_net_income_million = float(official["淨利（淨損）歸屬於母公司業主"]) / 1000

    result = {
        "documents": documents["count"],
        "minPeriod": documents["min_period"],
        "maxPeriod": documents["max_period"],
        "facts": facts,
        "reconciliations": reconciliations,
        "latestOfficialDifference": round(latest["parent_net_income"] - official_parent_net_income_million, 6),
        "queryPlan": query_plan,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    assert documents["count"] == 12
    assert facts > 10000
    assert all(all(abs(value) < 0.001 for key, value in item.items() if key.endswith("Difference")) for item in reconciliations)
    assert abs(result["latestOfficialDifference"]) < 0.001
    assert any("idx_quarterly_financials_company_basis_period" in row[-1] for row in query_plan)


if __name__ == "__main__":
    main()
