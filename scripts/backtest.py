"""全市場回測：在 2024、2025 年的 5 月底／8 月底／11 月底，用當時已公布的資料預估全年 EPS，和實際全年 EPS 比較。

比較的算法：
  主算法  ：旺淡季營收＋近四季淨利率（正式採用）
  簡易版  ：未公布月份用近 3 個月平均＋近四季淨利率（最早的 demo 算法）
  舊版    ：舊站 CT-EPS-Analytics（旺淡季營收＋今年已公布財報的毛利率、費用率、稅率＋業外近八季中位數）
  近四季  ：直接拿近四季 EPS 當全年
排除營建、金融保險業；實際全年 EPS 絕對值 < 0.5 元的公司不計百分比誤差（分母太小會失真）。
"""
from __future__ import annotations

import statistics as st
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from twdata.common import HISTORY, ROOT  # noqa: E402

POINTS = [("5 月底", 1, 4), ("8 月底", 2, 7), ("11 月底", 3, 10)]
YEARS = [2024, 2025]


def q_back(y, q, n):
    out = []
    for _ in range(n):
        out.append((y, q))
        q -= 1
        if q == 0:
            y, q = y - 1, 4
    return out


def seasonal(m, y, nm):
    f = [sum(m[(yy, k)] for k in range(1, 13)) / sum(m[(yy, k)] for k in range(1, nm + 1))
         for yy in range(y - 4, y) if all((yy, k) in m for k in range(1, 13)) and sum(m[(yy, k)] for k in range(1, nm + 1)) > 0]
    return st.median(f) if len(f) >= 2 else 12 / nm


def run_one(qd, m, y, nq, nm):
    need_q = q_back(y, nq, 8)
    if any(k not in qd for k in need_q[:4]) or any((y, k) not in m for k in range(1, nm + 1)):
        return None
    shares = qd[(y, nq)]["parent_net_income"] / qd[(y, nq)]["eps"] if qd[(y, nq)]["eps"] else None
    if not shares or shares <= 0:
        return None
    last4 = need_q[:4]
    rev4 = sum(qd[k]["revenue"] for k in last4)
    if not rev4:
        return None
    margin = sum(qd[k]["parent_net_income"] for k in last4) / rev4
    pub = sum(qd[(y, i)]["eps"] for i in range(1, nq + 1))
    stmt = sum(qd[(y, i)]["revenue"] for i in range(1, nq + 1))
    ytd = sum(m[(y, k)] for k in range(1, nm + 1))
    annual = ytd * seasonal(m, y, nm)
    main_ = pub + max(0, annual - stmt) * margin / shares
    avg3 = sum(m[(y, k)] for k in range(nm - 2, nm + 1)) / 3
    simple = pub + sum((sum(m[(y, k)] if k <= nm else avg3 for k in range(qq * 3 - 2, qq * 3 + 1))) for qq in range(nq + 1, 5)) * margin / shares
    # 舊版
    ytdq = [qd[(y, i)] for i in range(1, nq + 1)]
    S = lambda c: sum(d[c] or 0 for d in ytdq)  # noqa: E731
    gp, opex, nonop, pretax, tax, ni, pni = (S(c) for c in ("gross_profit", "operating_expense", "non_operating", "pretax_income", "income_tax", "net_income", "parent_net_income"))
    old = None
    if stmt and None not in [qd[(y, i)]["gross_profit"] for i in range(1, nq + 1)]:
        nci = ni - pni
        taxr = min(max(tax / pretax, 0), .5) if pretax > 0 else .2
        ncir = min(max(nci / ni, 0), .5) if ni > 0 else 0
        non8 = [qd[k]["non_operating"] for k in need_q if k in qd and qd[k]["non_operating"] is not None]
        rem_non = st.median(non8) * (4 - nq) if len(non8) >= 4 else nonop * (4 - nq) / nq
        rr = max(0, annual - stmt)
        pt = gp + rr * gp / stmt - (opex + rr * opex / stmt) + nonop + rem_non
        niy = pt - (pt * taxr if pt > 0 else 0)
        old = (niy - nci - max(0, niy - ni) * ncir) / shares
    ttm = sum(qd[k]["eps"] for k in last4)
    return {"主算法": main_, "簡易版": simple, "舊版": old, "近四季": ttm}


def main():
    co = pd.read_csv(HISTORY / "companies.csv", dtype={"code": str, "industry_code": str})
    keep = set(co[~co["industry_code"].isin(["14", "17"])]["code"])
    q = pd.read_csv(HISTORY / "quarterly.csv", dtype={"code": str})
    mr = pd.read_csv(HISTORY / "monthly_revenue.csv", dtype={"code": str})
    rows = []
    for code, g in q.groupby("code"):
        if code not in keep:
            continue
        qd = {(int(x.year), int(x.quarter)): x._asdict() for x in g.itertuples(index=False)
              if not any(pd.isna(getattr(x, c)) for c in ("revenue", "parent_net_income", "eps"))}
        m = {(int(x.year), int(x.month)): x.revenue for x in mr[mr["code"] == code].itertuples(index=False) if not pd.isna(x.revenue)}
        for y in YEARS:
            if not all((y, k) in qd for k in range(1, 5)):
                continue
            actual = sum(qd[(y, k)]["eps"] for k in range(1, 5))
            if abs(actual) < 0.5:
                continue
            for name, nq, nm in POINTS:
                try:
                    res = run_one(qd, m, y, nq, nm)
                except (ZeroDivisionError, TypeError, KeyError):
                    res = None
                if res and res["舊版"] is not None:
                    rows.append({"code": code, "year": y, "point": name, "actual": actual, **res})
    df = pd.DataFrame(rows)
    methods = ["主算法", "簡易版", "舊版", "近四季"]
    for k in methods:
        df[k + "_err"] = (df[k] - df["actual"]).abs() / df["actual"].abs()
    lines = ["# 全市場回測結果\n",
             f"樣本：上市櫃（排除營建、金融保險）{df['code'].nunique()} 家，2024、2025 兩年，三個時間點，共 {len(df)} 次預估。"
             "實際全年 EPS 絕對值 < 0.5 元者不列入。\n",
             "表內數字：誤差中位數／平均誤差（平均誤差已排除誤差 > 300% 的極端值）／誤差 < 10% 的比例。\n",
             "| 時間點 | " + " | ".join(methods) + " |", "|---|" + "---|" * len(methods)]
    for p in [x[0] for x in POINTS] + ["全部"]:
        s = df if p == "全部" else df[df["point"] == p]
        cells = []
        for k in methods:
            e = s[k + "_err"]
            cells.append(f"{e.median():.1%}／{e[e <= 3].mean():.1%}／{(e < .1).mean():.0%}")
        lines.append(f"| {p} | " + " | ".join(cells) + " |")
    best = (df[[k + "_err" for k in methods]].idxmin(axis=1).str.replace("_err", "").value_counts(normalize=True))
    lines.append("\n各算法「最準」的比例：" + "、".join(f"{k} {v:.0%}" for k, v in best.items()))
    # 依目前市值分組（市值＝最新收盤價 × 上市櫃股數）
    try:
        from twdata.common import LATEST
        px = pd.read_csv(LATEST / "prices.csv", dtype={"code": str}).set_index("code")["close"]
        cap = (px * co.set_index("code")["listed_shares"]).dropna().sort_values(ascending=False)
        tiers = [("市值前 200 大", set(cap.index[:200])), ("市值 201～600 名", set(cap.index[200:600])),
                 ("其他中小型股", set(df["code"]) - set(cap.index[:600]))]
        lines += ["", "## 依市值分組（誤差中位數）", "", "| 分組 | 家數 | 時間點 | " + " | ".join(methods) + " |",
                  "|---|---|---|" + "---|" * len(methods)]
        for lab, S in tiers:
            s = df[df["code"].isin(S)]
            for p in [x[0] for x in POINTS]:
                t = s[s["point"] == p]
                lines.append(f"| {lab} | {s['code'].nunique()} | {p} | " + " | ".join(f"{t[k + '_err'].median():.1%}" for k in methods) + " |")
    except FileNotFoundError:
        pass
    out = ROOT / "reports" / "backtest.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    df.to_csv(ROOT / "reports" / "backtest_detail.csv", index=False)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
