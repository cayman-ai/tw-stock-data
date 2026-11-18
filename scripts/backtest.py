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
    annual = max(ytd * seasonal(m, y, nm), ytd)
    # 三種推估一律先把月營收換算到財報口徑（與 build_site 相同），比較的才只是算法差異
    m_pub = sum(m[(y, k)] for k in range(1, 3 * nq + 1))       # 已公布季度對應月份的月營收
    ratio = stmt / m_pub
    rem_main = (annual - m_pub) * ratio                           # 剩餘營收（財報口徑）
    main_ = pub + rem_main * margin / shares
    avg3 = sum(m[(y, k)] for k in range(nm - 2, nm + 1)) / 3
    rem_simple = sum(sum(m[(y, k)] if k <= nm else avg3 for k in range(qq * 3 - 2, qq * 3 + 1)) for qq in range(nq + 1, 5)) * ratio
    simple = pub + rem_simple * margin / shares
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
        rr = rem_main                                             # 舊版同樣用財報口徑的剩餘營收
        pt = gp + rr * gp / stmt - (opex + rr * opex / stmt) + nonop + rem_non
        niy = pt - (pt * taxr if pt > 0 else 0)
        old = (niy - nci - max(0, niy - ni) * ncir) / shares
    ttm = sum(qd[k]["eps"] for k in last4)
    return {"主算法": main_, "簡易版": simple, "舊版": old, "近四季": ttm, "rev_ttm": rev4}


def main():
    co = pd.read_csv(HISTORY / "companies.csv", dtype={"code": str, "industry_code": str})
    # 排除營建、金融。已下市（不在目前名冊）的公司也納入，避免只看存活公司；
    # 這類公司查不到產業別，以「損益表有毛利欄位」判斷不是金融業。
    excluded = set(co[co["industry_code"].isin(["14", "17"])]["code"])
    q = pd.read_csv(HISTORY / "quarterly.csv", dtype={"code": str})
    mr = pd.read_csv(HISTORY / "monthly_revenue.csv", dtype={"code": str})
    rows = []
    for code, g in q.groupby("code"):
        if code in excluded or g["gross_profit"].isna().all():
            continue
        qd = {(int(x.year), int(x.quarter)): x._asdict() for x in g.itertuples(index=False)
              if not any(pd.isna(getattr(x, c)) for c in ("revenue", "parent_net_income", "eps"))}
        m = {(int(x.year), int(x.month)): x.revenue for x in mr[mr["code"] == code].itertuples(index=False) if not pd.isna(x.revenue)}
        for y in YEARS:
            full = all((y, k) in qd for k in range(1, 5))
            actual = sum(qd[(y, k)]["eps"] for k in range(1, 5)) if full else None
            for name, nq, nm in POINTS:
                try:
                    res = run_one(qd, m, y, nq, nm)
                except (ZeroDivisionError, TypeError, KeyError):
                    res = None
                if res:
                    rows.append({"code": code, "year": y, "point": name, "actual": actual, **res})
    allrows = pd.DataFrame(rows)
    # 營收規模排名：用預估當時所有有資料的公司排（在依年末實際 EPS 篩選之前），避免用到未來資訊
    allrows["rank_pct"] = allrows.groupby(["year", "point"])["rev_ttm"].rank(ascending=False, pct=True)
    df = allrows[allrows["actual"].notna() & (allrows["actual"].abs() >= 0.5) & allrows["舊版"].notna()].copy()
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
    # 依「預估當時」近四季營收規模分組（不用目前市值，避免偷看未來）
    lines += ["", "## 依預估當時的營收規模分組（誤差中位數）", "",
              "同一時點、同一年度內，依當時近四季營收在「當時所有有資料公司」中的排名：前 15%、15～45%、其餘。", "",
              "| 分組 | 時間點 | 樣本 | " + " | ".join(methods) + " |", "|---|---|---|" + "---|" * len(methods)]
    groups = [("大型（前 15%）", df["rank_pct"] <= .15), ("中型（15～45%）", (df["rank_pct"] > .15) & (df["rank_pct"] <= .45)),
              ("小型（其餘）", df["rank_pct"] > .45)]
    for lab, mask in groups:
        for p in [x[0] for x in POINTS]:
            t = df[mask & (df["point"] == p)]
            lines.append(f"| {lab} | {p} | {len(t)} | " + " | ".join(f"{t[k + '_err'].median():.1%}" for k in methods) + " |")
    lines += ["", "## 已知限制", "",
              "- 財報與月營收是現在抓到的版本，若公司事後更正，回測用的是更正後數字，可能比當時實際能拿到的資料略準。",
              "- 已下市公司有納入，但上市前、下市後的期間本來就沒有資料；已下市公司查不到產業別，以「損益表有毛利欄位」排除金融業，已下市營建業無法排除。",
              "- 這是用事後整理的資料做的比較，結論是「各算法相對準確度」，不是當時實際可得的精確誤差。",
              "- 各時點假設當月最後一天前，前一季財報與上月營收都已公布（依法定期限）；個別延遲申報的公司未逐一處理。"]
    out = ROOT / "reports" / "backtest.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    df.to_csv(ROOT / "reports" / "backtest_detail.csv", index=False)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
