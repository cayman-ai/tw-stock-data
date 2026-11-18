"""把 data/parts/ 的每期檔案整理成 data/history/ 的歷史表。

- 季報：官方彙總表是「累計值」，這裡轉成「單季值」（Q1＝累計，Qn＝本期累計−上期累計）。
  資產負債表是時點數，不用轉。前一季缺資料時，該季單季值留空（不估算）。
- 股利：依「股利所屬期間」歸到盈餘年度，同一盈餘年度的各次配發加總。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from twdata.common import HISTORY, PARTS  # noqa: E402

FLOW_IS = ["revenue", "gross_profit", "operating_expense", "operating_income", "non_operating", "pretax_income",
           "income_tax", "net_income", "parent_net_income", "nci_net_income", "eps"]
FLOW_CF = ["operating_cash_flow", "investing_cash_flow"]


def load(kind: str) -> pd.DataFrame:
    files = sorted((PARTS / kind).glob("*.csv"))
    if not files:
        return pd.DataFrame()
    df = pd.concat([pd.read_csv(f, dtype={"code": str}) for f in files], ignore_index=True)
    return df


def to_single(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    df = df.sort_values(["code", "year", "quarter"]).drop_duplicates(["code", "year", "quarter"], keep="last")
    prev = df.copy()
    prev["quarter"] = prev["quarter"] + 1
    m = df.merge(prev[["code", "year", "quarter"] + cols], on=["code", "year", "quarter"], how="left", suffixes=("", "_prev"))
    for c in cols:
        single = m[c] - m[c + "_prev"]
        m[c] = m[c].where(m["quarter"] == 1, single)
    return m[["code", "year", "quarter"] + cols]


def main():
    HISTORY.mkdir(parents=True, exist_ok=True)
    is_ = load("is")
    bs = load("bs")
    cf = load("cf")
    # 沒有子公司的公司，財報不分「歸屬母公司」：用總淨利、總權益代替
    is_["parent_net_income"] = is_["parent_net_income"].where(is_["parent_net_income"].notna(), is_["net_income"])
    bs["parent_equity"] = bs["parent_equity"].where(bs["parent_equity"].notna(), bs["total_equity"])
    # 累計基礎的隱含股數（千股）＝累計母公司淨利 ÷ 累計 EPS
    is_ = is_.sort_values(["code", "year", "quarter"])
    is_["_shares"] = is_["parent_net_income"] / is_["eps"].where(is_["eps"].abs() >= 0.05)
    cum = is_.set_index(["code", "year", "quarter"])
    q = to_single(is_, FLOW_IS)
    # 單季 EPS：同一年內股數基礎若大幅改變（拆股、面額變更），改用「單季淨利 ÷ 本期隱含股數」，不用累計相減
    q = q.merge(is_[["code", "year", "quarter", "_shares"]], on=["code", "year", "quarter"], how="left")
    prev = is_[["code", "year", "quarter", "_shares"]].copy()
    prev["quarter"] += 1
    q = q.merge(prev.rename(columns={"_shares": "_prev_shares"}), on=["code", "year", "quarter"], how="left")
    jump = (q["quarter"] > 1) & ((q["_shares"] / q["_prev_shares"]).sub(1).abs() > 0.5)
    q.loc[jump, "eps"] = q.loc[jump, "parent_net_income"] / q.loc[jump, "_shares"]
    # 股數基礎調整係數：各季隱含股數 ÷ 最新一季隱含股數；只有差距超過 20%（拆股、面額變更、大幅增減資或配股）才調整，其餘為 1
    latest = q.dropna(subset=["_shares"]).sort_values(["year", "quarter"]).groupby("code")["_shares"].last()
    q["share_factor"] = (q["_shares"] / q["code"].map(latest)).where(lambda x: (x > 1.2) | (x < 1 / 1.2), 1.0).fillna(1.0).round(6)
    q = q.drop(columns=["_shares", "_prev_shares"])
    q = q.merge(bs.drop_duplicates(["code", "year", "quarter"], keep="last")
                [["code", "year", "quarter", "total_assets", "total_liabilities", "parent_equity", "total_equity", "bvps"]],
                on=["code", "year", "quarter"], how="outer")
    if not cf.empty:
        q = q.merge(to_single(cf, FLOW_CF), on=["code", "year", "quarter"], how="left")
    q["eps"] = q["eps"].round(2)
    q.sort_values(["code", "year", "quarter"]).to_csv(HISTORY / "quarterly.csv", index=False)
    print(f"quarterly.csv：{q['code'].nunique()} 家、{len(q)} 筆")

    rev = load("revenue")
    if not rev.empty:
        rev = rev.drop_duplicates(["code", "year", "month"], keep="last").sort_values(["code", "year", "month"])
        rev.to_csv(HISTORY / "monthly_revenue.csv", index=False)
        print(f"monthly_revenue.csv：{rev['code'].nunique()} 家、{len(rev)} 筆")

    div = load("dividends")
    if not div.empty:
        div = div.drop_duplicates(["code", "period", "record_date"], keep="last")
        div["cash_dividend"] = div["cash_from_earnings"] + div["cash_from_capital"]
        div["stock_dividend"] = div["stock_from_earnings"] + div["stock_from_capital"]
        agg = (div.groupby(["code", "earnings_year"], as_index=False)
               .agg(cash_dividend=("cash_dividend", "sum"), stock_dividend=("stock_dividend", "sum"),
                    distributions=("period", "count"))
               .round({"cash_dividend": 4, "stock_dividend": 4}))
        agg.to_csv(HISTORY / "dividends.csv", index=False)
        print(f"dividends.csv：{agg['code'].nunique()} 家、{len(agg)} 筆")


if __name__ == "__main__":
    main()
