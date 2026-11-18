"""由 data/history 與 data/latest 計算網頁成品 data/site/stocks.json。算法見 docs/SPEC.md 第 4 節。

主算法（2026-11-18 回測選定）：今年預估 EPS ＝ 已公布季 EPS ＋ 剩餘營收 × 近四季淨利率 ÷ 股數
  全年營收 ＝ 今年已公布月營收累計 × 旺淡季係數（前 2～4 個完整年度「全年 ÷ 同期累計」中位數）
"""
from __future__ import annotations

import json
import math
import statistics as st
import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from twdata.common import HISTORY, LATEST, ROOT  # noqa: E402

EXCLUDED_INDUSTRY = {"14", "17"}          # 建材營造業、金融保險業
SITE = ROOT / "data" / "site"


def r(x, n=4):
    return None if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))) else round(x, n)


def div(a, b):
    if a is None or b is None or b == 0 or (isinstance(a, float) and math.isnan(a)) or (isinstance(b, float) and math.isnan(b)):
        return None
    return a / b


def q_back(y, q, n):
    out = []
    for _ in range(n):
        out.append((y, q))
        q -= 1
        if q == 0:
            y, q = y - 1, 4
    return out


def seasonal_factor(m: dict, Y: int, nm: int):
    """前 2～4 個完整年度「全年營收 ÷ 前 nm 個月累計」的中位數；不足 2 年回傳 None。"""
    f = []
    for yy in range(Y - 4, Y):
        if all((yy, k) in m for k in range(1, 13)):
            ytd = sum(m[(yy, k)] for k in range(1, nm + 1))
            if ytd > 0:
                f.append(sum(m[(yy, k)] for k in range(1, 13)) / ytd)
    return (st.median(f), len(f)) if len(f) >= 2 else (None, len(f))


def month_weights(m: dict, Y: int):
    """各月份占全年營收比重（前幾個完整年度中位數），用來把剩餘營收分配到各季。"""
    w = {}
    for k in range(1, 13):
        vals = []
        for yy in range(Y - 4, Y):
            if all((yy, j) in m for j in range(1, 13)):
                tot = sum(m[(yy, j)] for j in range(1, 13))
                if tot > 0:
                    vals.append(m[(yy, k)] / tot)
        w[k] = st.median(vals) if vals else 1 / 12
    return w


def forecast_eps(qd: dict, m: dict, Y: int, shares: float, latest_q: tuple):
    """回傳 (預估 EPS, 明細) 或 (None, 原因)。qd：{(y,q): row}，m：{(y,mo): 營收}。"""
    nq = max([q for (y, q) in qd if y == Y and qd[(y, q)].get("eps") is not None], default=0)
    nm = max([mo for (y, mo) in m if y == Y], default=0)
    if nm == 0:
        return None, "今年尚無月營收"
    if any((Y, k) not in m for k in range(1, nm + 1)):
        return None, "今年月營收有缺漏"
    ytd = sum(m[(Y, k)] for k in range(1, nm + 1))
    if nm == 12:
        annual, factor, nf = ytd, 1.0, 0
    else:
        factor, nf = seasonal_factor(m, Y, nm)
        annual = ytd * factor if factor else ytd / nm * 12
    stmt_rev = sum(qd[(Y, q)]["revenue"] or 0 for q in range(1, nq + 1))
    last4 = q_back(*latest_q, 4)
    if any(k not in qd for k in last4):
        return None, "近四季財報不完整"
    rev4 = sum(qd[k]["revenue"] or 0 for k in last4)
    ni4 = sum(qd[k]["parent_net_income"] or 0 for k in last4)
    margin = div(ni4, rev4)
    if margin is None:
        return None, "近四季營收為 0"
    pub = sum(qd[(Y, q)]["eps"] for q in range(1, nq + 1))
    remaining = max(0.0, annual - stmt_rev)
    # 剩餘營收分配到各季：已公布月份用實際，未公布月份依歷史月份比重分配
    w = month_weights(m, Y)
    unknown = [k for k in range(nm + 1, 13)]
    wsum = sum(w[k] for k in unknown) or 1
    rest = max(0.0, annual - ytd)
    est = []
    for q in range(nq + 1, 5):
        months = range(q * 3 - 2, q * 3 + 1)
        rev = sum(m[(Y, k)] if k <= nm else rest * w[k] / wsum for k in months)
        est.append({"q": q, "eps": r(rev * margin / shares, 2), "actual_months": sum(1 for k in months if k <= nm)})
    eps = pub + remaining * margin / shares
    return eps, {"published": [{"q": q, "eps": r(qd[(Y, q)]["eps"], 2)} for q in range(1, nq + 1)],
                 "estimated": est, "margin_ttm": r(margin), "seasonal_factor": r(factor) if factor else None,
                 "seasonal_years": nf, "annual_revenue_est": r(annual, 0), "months_published": nm}


def main():
    co = pd.read_csv(HISTORY / "companies.csv", dtype={"code": str, "industry_code": str})
    q = pd.read_csv(HISTORY / "quarterly.csv", dtype={"code": str})
    mr = pd.read_csv(HISTORY / "monthly_revenue.csv", dtype={"code": str})
    dv = pd.read_csv(HISTORY / "dividends.csv", dtype={"code": str})
    px = pd.read_csv(LATEST / "prices.csv", dtype={"code": str})
    Y = int(mr["year"].max())
    latest_month = mr[mr["year"] == Y]["month"].max()
    gq = q.dropna(subset=["eps"])
    gy, gqq = gq.sort_values(["year", "quarter"]).iloc[-1][["year", "quarter"]]

    qg = {c: g for c, g in q.groupby("code")}
    mg = {c: g for c, g in mr.groupby("code")}
    dg = {c: g for c, g in dv.groupby("code")}
    pxd = px.set_index("code").to_dict("index")
    out = []
    for _, c in co.iterrows():
        code = c["code"]
        rec = {"code": code, "name": c["name"], "market": c["market"], "industry": c["industry"]}
        qd = {(int(x.year), int(x.quarter)): {k: (None if pd.isna(v) else v) for k, v in x._asdict().items()}
              for x in qg.get(code, pd.DataFrame(columns=q.columns)).itertuples(index=False)}
        for v in qd.values():   # 每股數字一律換算到目前股數（拆股、面額變更、大幅增資後才可比較）
            if v.get("eps") is not None and v.get("share_factor"):
                v["eps_reported"], v["eps"] = v["eps"], v["eps"] * v["share_factor"]
        m = {(int(x.year), int(x.month)): x.revenue for x in mg.get(code, pd.DataFrame(columns=mr.columns)).itertuples(index=False)
             if not pd.isna(x.revenue)}
        p = pxd.get(code, {})
        price = p.get("close") if p and not pd.isna(p.get("close")) else None
        rec.update(price=price, pe=p.get("pe") if p and not pd.isna(p.get("pe")) else None,
                   pb=p.get("pb") if p and not pd.isna(p.get("pb")) else None)
        withq = [k for k, v in qd.items() if v.get("eps") is not None]
        if not withq:
            rec["error"] = "無財報資料"
            out.append(rec)
            continue
        lq = max(withq)
        last4 = q_back(*lq, 4)
        full4 = all(k in qd and qd[k].get("eps") is not None for k in last4)
        ttm_eps = sum(qd[k]["eps"] for k in last4) if full4 else None
        ni4 = sum(qd[k]["parent_net_income"] or 0 for k in last4) if full4 else None
        rev4 = sum(qd[k]["revenue"] or 0 for k in last4) if full4 else None
        lv = qd[lq]
        shares = div(lv.get("parent_net_income"), lv.get("eps")) if lv.get("eps") and abs(lv["eps"]) >= 0.05 else None
        if not shares or shares <= 0:
            shares = c["listed_shares"] / 1000 if not pd.isna(c["listed_shares"]) else None   # 千股
        eps_f, detail = (None, "缺少股數") if not shares else forecast_eps(qd, m, Y, shares, lq) if full4 else (None, "近四季財報不完整")
        ly = [qd.get((Y - 1, k), {}).get("eps") for k in range(1, 5)]
        last_year_eps = sum(ly) if all(v is not None for v in ly) else None
        rec["eps"] = {"forecast": r(eps_f, 2), "last_year": r(last_year_eps, 2), "ttm": r(ttm_eps, 2),
                      "growth": r(div(eps_f, last_year_eps) - 1) if eps_f is not None and last_year_eps and last_year_eps > 0 else None,
                      "shares_thousand": r(shares, 0), "latest_quarter": f"{lq[0]}Q{lq[1]}"}
        if isinstance(detail, dict):
            rec["eps"].update(detail)
        else:
            rec["eps"]["note"] = detail
        # ROE（近四季）
        q4ago = qd.get(q_back(*lq, 5)[-1], {})
        eq_avg = (lv.get("parent_equity") + q4ago.get("parent_equity")) / 2 if lv.get("parent_equity") and q4ago.get("parent_equity") else None
        rec["roe_ttm"] = r(div(ni4, eq_avg))
        rec["external_roe"] = r(div(eps_f, price)) if eps_f is not None else None
        # 股利：近 5 年平均配發率（EPS ≤ 0 的年度不列入）
        dd = {int(x.earnings_year): x for x in dg.get(code, pd.DataFrame(columns=dv.columns)).itertuples(index=False)}
        hist = []
        for yy in range(Y - 1, Y - 6, -1):
            e = [qd.get((yy, k), {}).get("eps") for k in range(1, 5)]
            if any(v is None for v in e):
                continue
            ey = sum(e)
            d = dd.get(yy)
            fac = st.mean(qd[(yy, k)].get("share_factor") or 1 for k in range(1, 5))
            cash = float(d.cash_dividend) * fac if d is not None else 0.0
            stock = float(d.stock_dividend) * fac if d is not None else 0.0
            hist.append({"y": yy, "eps": r(ey, 2), "cash": r(cash, 4), "stock": r(stock, 4)})
        valid = [h for h in hist if h["eps"] and h["eps"] > 0]
        cr = st.mean(h["cash"] / h["eps"] for h in valid) if valid else None
        sr = st.mean(h["stock"] / h["eps"] for h in valid) if valid else None
        cash_f = eps_f * cr if eps_f is not None and eps_f > 0 and cr is not None else None
        streak = 0
        for h in hist:
            if h["cash"] > 0:
                streak += 1
            else:
                break
        rec["dividend"] = {"cash": r(cash_f, 2), "stock": r(eps_f * sr, 2) if cash_f is not None and sr is not None else None,
                           "cash_ratio": r(cr), "stock_ratio": r(sr), "years_used": len(valid),
                           "yield": r(div(cash_f, price)), "history": hist}
        # 營收
        lm = max(m) if m else None
        mrow = mg[code].set_index(["year", "month"]).loc[lm] if lm else None
        rec["revenue"] = {"latest_month": f"{lm[0]}-{lm[1]:02d}" if lm else None,
                          "mom_yoy": r(div(mrow["revenue"], mrow["last_year_revenue"]) - 1) if lm is not None and mrow["last_year_revenue"] else None,
                          "ytd_yoy": r(div(mrow["ytd_revenue"], mrow["last_year_ytd_revenue"]) - 1) if lm is not None and mrow["last_year_ytd_revenue"] else None}
        # 自由現金流（營業現金流＋投資現金流）
        fcf4 = sum((qd[k].get("operating_cash_flow") or 0) + (qd[k].get("investing_cash_flow") or 0) for k in last4) if full4 else None
        ocf4 = sum(qd[k].get("operating_cash_flow") or 0 for k in last4) if full4 else None
        pos = 0
        for yy in range(Y - 1, Y - 6, -1):
            ks = [(yy, k) for k in range(1, 5)]
            if all(k in qd and qd[k].get("operating_cash_flow") is not None for k in ks):
                pos += sum((qd[k]["operating_cash_flow"] or 0) + (qd[k].get("investing_cash_flow") or 0) for k in ks) > 0
        fps = div(fcf4, shares)
        rec["fcf"] = {"per_share_ttm": r(fps, 2), "yield": r(div(fps, price)), "positive_years_5y": pos}
        gp4 = sum(qd[k].get("gross_profit") or 0 for k in last4) if full4 else None
        op4 = sum(qd[k].get("operating_income") or 0 for k in last4) if full4 else None
        nit4 = sum(qd[k].get("net_income") or 0 for k in last4) if full4 else None
        rec["quality"] = {"gross_margin": r(div(gp4, rev4)), "operating_margin": r(div(op4, rev4)),
                          "cash_conversion": r(div(ocf4, nit4)) if nit4 and nit4 > 0 else None,
                          "debt_ratio": r(div(lv.get("total_liabilities"), lv.get("total_assets"))), "dividend_streak": streak}
        # 可信度：近 8 季單季淨利率波動大，或近 8 季有虧損季
        margins = [div(qd[k]["parent_net_income"], qd[k]["revenue"]) for k in q_back(*lq, 8)
                   if k in qd and qd[k].get("revenue") and qd[k].get("parent_net_income") is not None]
        low_conf = len(margins) < 8 or st.pstdev(margins) > 0.05 or any(x < 0 for x in margins)
        rec["flags"] = {"excluded_industry": c["industry_code"] in EXCLUDED_INDUSTRY,
                        "loss": (ttm_eps is not None and ttm_eps <= 0) or (eps_f is not None and eps_f <= 0),
                        "ytd_revenue_decline": rec["revenue"]["ytd_yoy"] is not None and rec["revenue"]["ytd_yoy"] < 0,
                        "low_confidence": bool(low_conf)}
        out.append(rec)

    SITE.mkdir(parents=True, exist_ok=True)
    doc = {"as_of": date.today().isoformat(), "target_year": Y,
           "sources_date": {"monthly_revenue": f"{Y}-{int(latest_month):02d}", "quarterly": f"{int(gy)}Q{int(gqq)}",
                            "price": str(px["date"].max())},
           "method": "今年預估 EPS＝已公布季 EPS＋剩餘營收×近四季淨利率÷股數；全年營收用旺淡季係數推估；自由現金流＝營業現金流＋投資現金流",
           "stocks": out}
    (SITE / "stocks.json").write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":"), default=lambda o: o.item() if hasattr(o, "item") else str(o)), encoding="utf-8")
    ok = sum(1 for s in out if s.get("eps", {}).get("forecast") is not None)
    print(f"stocks.json：{len(out)} 家，有預估 EPS {ok} 家，{(SITE / 'stocks.json').stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
