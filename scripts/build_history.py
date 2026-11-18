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
    parts = []
    for f in files:
        d = pd.read_csv(f, dtype={"code": str})
        d["_market"] = f.stem.split("_")[0]          # 檔名前綴：sii＝上市、otc＝上櫃
        parts.append(d)
    return pd.concat(parts, ignore_index=True)


CONFLICTS: list[dict] = []


def dedupe(df: pd.DataFrame, keys: list[str], kind: str, current_market: dict) -> pd.DataFrame:
    """同一公司同一期在上市、上櫃兩邊都有資料（轉市場）時：數字相同就保留一筆；
    不同就保留目前所屬市場那筆，並記錄到 reports/conflicts.csv，不靠檔名排序決定。"""
    dup = df.duplicated(keys, keep=False)
    if not dup.any():
        return df.drop(columns="_market")
    d = df[dup]
    for k, g in d.groupby(keys):
        vals = g.drop(columns="_market").drop_duplicates()
        if len(vals) > 1:
            CONFLICTS.append({"kind": kind, "key": "/".join(map(str, k)), "markets": ",".join(g["_market"]),
                              "kept": current_market.get(g["code"].iloc[0], "")})
    df = df.assign(_pref=(df["_market"] == df["code"].map(current_market)).astype(int))
    df = df.sort_values("_pref").drop_duplicates(keys, keep="last")
    return df.drop(columns=["_market", "_pref"])


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
    co = pd.read_csv(HISTORY / "companies.csv", dtype={"code": str, "report_type": str})
    mk = {"上市": "sii", "上櫃": "otc"}
    current_market = {c: mk.get(m, "") for c, m in zip(co["code"], co["market"])}
    keys = ["code", "year", "quarter"]
    is_ = dedupe(load("is"), keys, "is", current_market)
    bs = dedupe(load("bs"), keys, "bs", current_market)
    cf = dedupe(load("cf"), keys, "cf", current_market)
    # 只有官方公司資料登記為「個體報表」（編制財務報表類型＝2，沒有子公司）的公司，
    # 才用總淨利、總權益代替「歸屬母公司」；其他公司缺值就留空，不猜。
    solo = set(co.loc[co["report_type"] == "2", "code"]) if "report_type" in co else set()
    m = is_["code"].isin(solo) & is_["parent_net_income"].isna()
    is_.loc[m, "parent_net_income"] = is_.loc[m, "net_income"]
    m = bs["code"].isin(solo) & bs["parent_equity"].isna()
    bs.loc[m, "parent_equity"] = bs.loc[m, "total_equity"]
    # 累計基礎的隱含股數（千股）＝累計母公司淨利 ÷ 累計 EPS
    is_ = is_.sort_values(["code", "year", "quarter"])
    is_["_shares"] = is_["parent_net_income"] / is_["eps"].where(is_["eps"].abs() >= 0.3)  # EPS 太小時四捨五入誤差大，不推股數
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
        rev = dedupe(rev, ["code", "year", "month"], "revenue", current_market).sort_values(["code", "year", "month"])
        rev.to_csv(HISTORY / "monthly_revenue.csv", index=False)
        print(f"monthly_revenue.csv：{rev['code'].nunique()} 家、{len(rev)} 筆")

    div = load("dividends")
    if not div.empty:
        div = div.drop(columns="_market").drop_duplicates(["code", "period", "record_date"], keep="last")
        div["cash_dividend"] = div["cash_from_earnings"] + div["cash_from_capital"]
        div["stock_dividend"] = div["stock_from_earnings"] + div["stock_from_capital"]
        agg = (div.groupby(["code", "earnings_year"], as_index=False)
               .agg(cash_dividend=("cash_dividend", "sum"), stock_dividend=("stock_dividend", "sum"),
                    distributions=("period", "count"))
               .round({"cash_dividend": 4, "stock_dividend": 4}))
        agg.to_csv(HISTORY / "dividends.csv", index=False)
        print(f"dividends.csv：{agg['code'].nunique()} 家、{len(agg)} 筆")


def write_conflicts():
    out = HISTORY.parent.parent / "reports" / "conflicts.csv"
    out.parent.mkdir(exist_ok=True)
    pd.DataFrame(CONFLICTS, columns=["kind", "key", "markets", "kept"]).to_csv(out, index=False)
    print(f"上市／上櫃重複且數字不同：{len(CONFLICTS)} 筆（reports/conflicts.csv）")


if __name__ == "__main__":
    main()
    write_conflicts()
