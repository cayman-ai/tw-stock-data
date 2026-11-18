"""由 data/history 與 data/latest 計算網頁成品 data/site/stocks.json。算法見 docs/SPEC.md 第 4 節。

原則：缺資料就輸出 null 並寫明原因，不用 0 補。

主算法：今年預估 EPS ＝ 已公布季 EPS ＋ 剩餘營收 × 近四季淨利率（業外調整）÷ 股數
  全年營收（月營收口徑）＝ 今年已公布月營收累計 × 旺淡季係數（前 2～4 個完整年度「全年 ÷ 同期累計」中位數）
  月營收與財報營收口徑不同的公司（例如財報採淨額認列），用「已公布季度的財報營收 ÷ 同期月營收」換算到財報口徑。
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
# 預估區間寬度：全市場回測（reports/backtest.md）主算法的誤差中位數，依營收規模與已公布月營收月數
# 1～3 月（只有少數月份）用 5 月底的誤差再放寬 1.3 倍
RANGE = {"大型": (0.19, 0.11, 0.06), "中型": (0.23, 0.14, 0.08), "小型": (0.33, 0.21, 0.12)}


def range_pct(tier: str, months: int):
    may, aug, nov = RANGE[tier]
    if months <= 3:
        return may * 1.3
    if months <= 6:
        return may
    if months <= 9:
        return aug
    return nov
SITE = ROOT / "data" / "site"


def ok(x):
    return x is not None and not (isinstance(x, float) and (math.isnan(x) or math.isinf(x)))


def r(x, n=4):
    return round(float(x), n) if ok(x) else None


def div(a, b):
    return a / b if ok(a) and ok(b) and b != 0 else None


def q_back(y, q, n):
    out = []
    for _ in range(n):
        out.append((y, q))
        q -= 1
        if q == 0:
            y, q = y - 1, 4
    return out


def total(qd, keys, field):
    """多季加總；任何一季缺該欄位就回傳 None（不把缺值當 0）。"""
    vals = [qd.get(k, {}).get(field) for k in keys]
    return sum(vals) if all(ok(v) for v in vals) else None


def seasonal_factor(m: dict, Y: int, nm: int):
    f = []
    for yy in range(Y - 4, Y):
        if all((yy, k) in m for k in range(1, 13)):
            ytd = sum(m[(yy, k)] for k in range(1, nm + 1))
            if ytd > 0:
                f.append(sum(m[(yy, k)] for k in range(1, 13)) / ytd)
    return (st.median(f), len(f)) if len(f) >= 2 else (None, len(f))


def month_weights(m: dict, Y: int):
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
    """回傳 (預估 EPS, 明細 dict) 或 (None, 原因字串)。"""
    pub_q = sorted(q for (y, q) in qd if y == Y and ok(qd[(y, q)].get("eps")))
    nq = len(pub_q)
    if pub_q != list(range(1, nq + 1)):
        return None, "今年已公布季度不連續"
    if any(not ok(qd[(Y, q)].get("revenue")) for q in pub_q):
        return None, "今年財報營收缺漏"
    nm = max([mo for (y, mo) in m if y == Y], default=0)
    if nm == 0:
        return None, "今年尚無月營收"
    if any((Y, k) not in m for k in range(1, nm + 1)):
        return None, "今年月營收有缺漏"
    if nm < 3 * nq:
        return None, "月營收比財報還舊"
    last4 = q_back(*latest_q, 4)
    rev4, ni4 = total(qd, last4, "revenue"), total(qd, last4, "parent_net_income")
    margin = div(ni4, rev4)
    if margin is None:
        return None, "近四季營收或母公司淨利缺漏"
    # 業外調整（2026-11-18 全市場回測勝出）：近四季業外損益換成「近八季單季中位數 × 4」，
    # 扣掉一次性業外收益／損失（例如處分資產）後再算淨利率，避免把一次性項目外推到未來季度
    margin_raw, nonop_adj = margin, False
    non8 = [qd[k]["non_operating"] for k in q_back(*latest_q, 8) if ok(qd.get(k, {}).get("non_operating"))]
    non4, pt4, tx4, n4 = (total(qd, last4, f) for f in ("non_operating", "pretax_income", "income_tax", "net_income"))
    if len(non8) >= 6 and None not in (non4, pt4, n4) and n4 != 0:
        taxr = min(max((tx4 or 0) / pt4, 0), .5) if pt4 > 0 else .2
        share = min(max(ni4 / n4, 0), 1) if n4 > 0 else 1
        margin = (ni4 - (non4 - 4 * st.median(non8)) * (1 - taxr) * share) / rev4
        nonop_adj = True
    # 月營收 → 財報口徑換算比例
    if nq > 0:
        ratio = div(sum(qd[(Y, q)]["revenue"] for q in pub_q), sum(m[(Y, k)] for k in range(1, 3 * nq + 1)))
    else:
        ly = [(Y - 1, q) for q in range(1, 5)]
        ratio = div(total(qd, ly, "revenue"), sum(m[(Y - 1, k)] for k in range(1, 13))) if all((Y - 1, k) in m for k in range(1, 13)) else None
    if ratio is None or ratio <= 0:
        return None, "無法比對月營收與財報營收口徑"
    ytd = sum(m[(Y, k)] for k in range(1, nm + 1))
    if nm == 12:
        annual_m, factor, nf = ytd, 1.0, 0
    else:
        factor, nf = seasonal_factor(m, Y, nm)
        annual_m = ytd * factor if factor else ytd / nm * 12
    annual_m = max(annual_m, ytd)
    # 剩餘季度（月營收口徑）：已公布月份用實際，未公布月份依歷史月份比重分配，再換算到財報口徑
    w = month_weights(m, Y)
    unknown = list(range(nm + 1, 13))
    wsum = sum(w[k] for k in unknown) or 1
    rest = annual_m - ytd
    est, rem_eps = [], 0.0
    for q in range(nq + 1, 5):
        months = range(q * 3 - 2, q * 3 + 1)
        rev = ratio * sum(m[(Y, k)] if k <= nm else rest * w[k] / wsum for k in months)
        e = rev * margin / shares
        rem_eps += e
        est.append({"q": q, "eps": r(e, 2), "actual_months": sum(1 for k in months if k <= nm)})
    pub = sum(qd[(Y, q)]["eps"] for q in pub_q)
    return pub + rem_eps, {"published": [{"q": q, "eps": r(qd[(Y, q)]["eps"], 2)} for q in pub_q],
                           "estimated": est, "margin_ttm": r(margin), "margin_ttm_raw": r(margin_raw), "nonop_adjusted": nonop_adj, "seasonal_factor": r(factor) if factor else None,
                           "seasonal_years": nf, "revenue_basis_ratio": r(ratio), "months_published": nm}


def main():
    co = pd.read_csv(HISTORY / "companies.csv", dtype={"code": str, "industry_code": str})
    q = pd.read_csv(HISTORY / "quarterly.csv", dtype={"code": str})
    mr = pd.read_csv(HISTORY / "monthly_revenue.csv", dtype={"code": str})
    dv = pd.read_csv(HISTORY / "dividends.csv", dtype={"code": str})
    px = pd.read_csv(LATEST / "prices.csv", dtype={"code": str})
    cap_path = ROOT / "data" / "parts" / "capex" / "capex.csv"
    capex = {}
    if cap_path.exists():   # 資本支出（年初至季末累計，千元，正數）
        for x in pd.read_csv(cap_path, dtype={"code": str}).itertuples(index=False):
            if ok(x.capex_cum):
                capex[(x.code, int(x.year), int(x.quarter))] = float(x.capex_cum)
    # 全年營業／投資現金流：直接用第 4 季累計數（＝年報全年），不必四季單季都齊
    annual_cf = {}
    for f in (ROOT / "data" / "parts" / "cf").glob("*Q4.csv"):
        for x in pd.read_csv(f, dtype={"code": str}).itertuples(index=False):
            annual_cf[(x.code, int(x.year))] = (x.operating_cash_flow if ok(x.operating_cash_flow) else None,
                                               x.investing_cash_flow if ok(x.investing_cash_flow) else None)
    Y = int(mr["year"].max())
    latest_month = int(mr[mr["year"] == Y]["month"].max())
    gq = q.dropna(subset=["eps"]).sort_values(["year", "quarter"]).iloc[-1]
    max_div_year = int(dv["earnings_year"].max())

    qg = {c: g for c, g in q.groupby("code")}
    mg = {c: g for c, g in mr.groupby("code")}
    dg = {c: g for c, g in dv.groupby("code")}
    pxd = px.set_index("code").to_dict("index")
    out = []
    for _, c in co.iterrows():
        code = c["code"]
        rec = {"code": code, "name": c["name"], "market": c["market"], "industry": c["industry"]}
        qd = {}
        for x in qg.get(code, pd.DataFrame(columns=q.columns)).itertuples(index=False):
            v = {k: (val if ok(val) else None) for k, val in x._asdict().items()}
            if v.get("eps") is not None:   # 每股數字換算到目前股數（拆股、面額變更、大幅增資後才可比較）
                v["eps_reported"], v["eps"] = v["eps"], v["eps"] * (v.get("share_factor") or 1)
            qd[(int(x.year), int(x.quarter))] = v
        m = {(int(x.year), int(x.month)): float(x.revenue) for x in mg.get(code, pd.DataFrame(columns=mr.columns)).itertuples(index=False) if ok(x.revenue)}
        p = pxd.get(code, {})
        price = p.get("close") if ok(p.get("close")) and p.get("close") > 0 else None
        rec.update(price=price, pe=p.get("pe") if ok(p.get("pe")) else None, pb=p.get("pb") if ok(p.get("pb")) else None)
        withq = [k for k, v in qd.items() if v.get("eps") is not None]
        if not withq:
            rec["error"] = "無財報資料"
            out.append(rec)
            continue
        lq = max(withq)
        last4 = q_back(*lq, 4)
        lv = qd[lq]
        ttm_eps = total(qd, last4, "eps")
        ni4, rev4 = total(qd, last4, "parent_net_income"), total(qd, last4, "revenue")
        # 股數（千股）：最新一季母公司淨利 ÷ EPS（EPS 太小時誤差大，改用上市櫃股數）
        shares = div(lv.get("parent_net_income"), lv.get("eps_reported")) if lv.get("eps_reported") and abs(lv["eps_reported"]) >= 0.3 else None
        if not shares or shares <= 0:
            shares = c["listed_shares"] / 1000 if ok(c["listed_shares"]) else None
        if not shares:
            eps_f, detail = None, "缺少股數"
        else:
            eps_f, detail = forecast_eps(qd, m, Y, shares, lq)
        ly = total(qd, [(Y - 1, k) for k in range(1, 5)], "eps")
        rec["eps"] = {"target_year": Y, "forecast": r(eps_f, 2), "last_year": r(ly, 2), "ttm": r(ttm_eps, 2),
                      "growth": r(div(eps_f, ly) - 1) if eps_f is not None and ly is not None and ly >= 0.5 else None,   # 去年 EPS < 0.5 元基期太低，成長率失真，不算
                      "shares_thousand": r(shares, 0), "latest_quarter": f"{lq[0]}Q{lq[1]}"}
        if isinstance(detail, dict):
            rec["eps"].update(detail)
        else:
            rec["eps"]["note"] = detail
        # ROE（近四季）
        eq_now, eq_ago = lv.get("parent_equity"), qd.get(q_back(*lq, 5)[-1], {}).get("parent_equity")
        rec["roe_ttm"] = r(div(ni4, (eq_now + eq_ago) / 2)) if ok(eq_now) and ok(eq_ago) and eq_now + eq_ago > 0 else None
        rec["external_roe"] = r(div(eps_f, price))
        # 股利：近 5 年平均配發率（EPS ≤ 0 年度不列入）
        # 除權息彙總涵蓋所有實際配發；資料中最新的盈餘年度可能還沒配完，沒有紀錄時視為「未知」，較早年度沒有紀錄才視為未配發。
        dd = {int(x.earnings_year): x for x in dg.get(code, pd.DataFrame(columns=dv.columns)).itertuples(index=False)}
        hist = []
        for yy in range(Y - 1, Y - 6, -1):
            ey = total(qd, [(yy, k) for k in range(1, 5)], "eps")
            if ey is None:
                hist.append({"y": yy, "eps": None, "cash": None, "stock": None, "status": "缺財報"})
                continue
            fac = st.mean(qd[(yy, k)].get("share_factor") or 1 for k in range(1, 5))
            d = dd.get(yy)
            if d is not None:
                hist.append({"y": yy, "eps": r(ey, 2), "cash": r(float(d.cash_dividend) * fac, 4), "stock": r(float(d.stock_dividend) * fac, 4), "status": "已配發"})
            elif yy >= max_div_year - 1 and yy >= Y - 1:
                hist.append({"y": yy, "eps": r(ey, 2), "cash": None, "stock": None, "status": "尚未配發或資料未到"})
            else:
                hist.append({"y": yy, "eps": r(ey, 2), "cash": 0.0, "stock": 0.0, "status": "未配發"})
        # 單年配發率超過 150%（多為動用公積配發或當年獲利特別少）不列入平均，避免少數年度把平均拉爆
        valid = [h for h in hist if h["eps"] is not None and h["eps"] > 0 and h["cash"] is not None
                 and (h["cash"] + (h["stock"] or 0)) / h["eps"] <= 1.5]
        cr = st.mean(h["cash"] / h["eps"] for h in valid) if valid else None
        sr = st.mean(h["stock"] / h["eps"] for h in valid) if valid else None
        pos_eps = eps_f is not None and eps_f > 0
        cash_f = eps_f * cr if pos_eps and cr is not None else None
        stock_f = eps_f * sr if pos_eps and sr is not None else None
        # 連續配息年數：從「最近一個已確認的年度」往前數，遇到未知或未配發就停止；同時輸出截至年度
        streak, streak_through = 0, None
        for i, h in enumerate(hist):
            if i == 0 and h["status"] == "尚未配發或資料未到":
                continue
            if h["cash"] is None or h["cash"] <= 0:
                break
            streak += 1
            streak_through = streak_through or h["y"]
        rec["dividend"] = {"cash": r(cash_f, 2), "stock": r(stock_f, 2), "cash_ratio": r(cr), "stock_ratio": r(sr),
                           "years_used": len(valid), "yield": r(div(cash_f, price)), "history": hist}
        # 營收
        lm = max(m) if m else None
        rv = {"latest_month": None, "mom_yoy": None, "ytd_yoy": None}
        if lm:
            row = mg[code].set_index(["year", "month"]).loc[lm]
            g1, g2 = div(row["revenue"], row["last_year_revenue"]), div(row["ytd_revenue"], row["last_year_ytd_revenue"])
            rv = {"latest_month": f"{lm[0]}-{lm[1]:02d}", "mom_yoy": r(g1 - 1) if g1 is not None else None,
                  "ytd_yoy": r(g2 - 1) if g2 is not None else None}
        rec["revenue"] = rv
        # 簡易自由現金流＝營業現金流＋投資現金流（官方彙總表沒有資本支出；投資現金流含金融資產買賣，為近似值）
        ocf4, icf4 = total(qd, last4, "operating_cash_flow"), total(qd, last4, "investing_cash_flow")
        fcf4 = ocf4 + icf4 if ocf4 is not None and icf4 is not None else None
        pos, known = 0, 0
        for yy in range(Y - 1, Y - 6, -1):
            o, i = annual_cf.get((code, yy), (None, None))
            if o is not None and i is not None:
                known += 1
                pos += (o + i) > 0
        fps = div(fcf4, shares)
        # 自由現金流＝營業現金流−資本支出（資本支出來自逐家現金流量表；年初至季末累計換算成近四季／全年）
        ly_, lq_ = lq
        if lq_ == 4:
            capex4 = capex.get((code, ly_, 4))
        else:
            a1, a2, a3 = capex.get((code, ly_, lq_)), capex.get((code, ly_ - 1, 4)), capex.get((code, ly_ - 1, lq_))
            capex4 = a1 + a2 - a3 if None not in (a1, a2, a3) else None
        fcf_true = ocf4 - capex4 if ocf4 is not None and capex4 is not None else None
        fps_true = div(fcf_true, shares)
        tpos, tknown = 0, 0
        for yy in range(Y - 1, Y - 6, -1):
            o = annual_cf.get((code, yy), (None, None))[0]
            cx = capex.get((code, yy, 4))
            if o is not None and cx is not None:
                tknown += 1
                tpos += (o - cx) > 0
        rec["fcf"] = {"per_share_ttm": r(fps_true, 2), "yield": r(div(fps_true, price)), "capex_ttm": r(capex4, 0),
                      "positive_years_5y": tpos if tknown == 5 else None, "known_years": tknown,
                      "note": None if fcf_true is not None else "資本支出資料尚未抓到"}
        rec["fcf_approx"] = {"basis": "營業現金流＋投資現金流（近似，含金融資產買賣）", "per_share_ttm": r(fps, 2),
                             "yield": r(div(fps, price)), "positive_years_5y": pos if known == 5 else None, "known_years": known}
        gp4, op4, nit4 = total(qd, last4, "gross_profit"), total(qd, last4, "operating_income"), total(qd, last4, "net_income")
        rec["quality"] = {"gross_margin": r(div(gp4, rev4)), "operating_margin": r(div(op4, rev4)),
                          "cash_conversion": r(div(ocf4, nit4)) if nit4 is not None and nit4 > 0 else None,
                          "debt_ratio": r(div(lv.get("total_liabilities"), lv.get("total_assets"))),
                          "dividend_streak": streak, "dividend_streak_through": streak_through}
        # 可信度低：近 8 季單季淨利率波動大、近 8 季有虧損季、營收年增率波動大、或月營收與財報口徑差距大
        k8 = q_back(*lq, 8)
        margins = [div(qd[k].get("parent_net_income"), qd[k].get("revenue")) for k in k8 if k in qd]
        margins = [x for x in margins if x is not None]
        yoys = [div(qd.get(k, {}).get("revenue"), qd.get((k[0] - 1, k[1]), {}).get("revenue")) for k in k8]
        yoys = [x - 1 for x in yoys if x is not None]
        ratio = rec["eps"].get("revenue_basis_ratio")
        reasons = []
        if len(margins) < 8:
            reasons.append("財報季數不足 8 季")
        elif st.pstdev(margins) > 0.05:
            reasons.append("淨利率波動大")
        if any(x < 0 for x in margins):
            reasons.append("近 8 季有虧損")
        if len(yoys) >= 6 and st.pstdev(yoys) > 0.3:
            reasons.append("營收年增率波動大")
        if ratio is not None and not 0.8 <= ratio <= 1.25:
            reasons.append("月營收與財報營收口徑差異大")
        rec["flags"] = {"excluded_industry": c["industry_code"] in EXCLUDED_INDUSTRY,
                        "loss": bool((ttm_eps is not None and ttm_eps <= 0) or (eps_f is not None and eps_f <= 0)),
                        "ytd_revenue_decline": bool(rv["ytd_yoy"] is not None and rv["ytd_yoy"] < 0),
                        "low_confidence": bool(reasons), "low_confidence_reasons": reasons}
        rec["_rev4"] = rev4
        out.append(rec)

    revs = sorted(((s["_rev4"], s["code"]) for s in out if s.get("_rev4")), reverse=True)
    rank = {code: i / len(revs) for i, (_, code) in enumerate(revs)}
    for s_ in out:
        s_.pop("_rev4", None)
        e = s_.get("eps")
        if not e:
            continue
        pr = rank.get(s_["code"])
        tier = None if pr is None else "大型" if pr < .15 else "中型" if pr < .45 else "小型"
        e["size_tier"] = tier
        if tier and e.get("forecast") is not None and e.get("months_published"):
            pct = range_pct(tier, e["months_published"])
            if s_["flags"]["low_confidence"]:
                pct *= 1.5
            f = e["forecast"]
            band = abs(f) * pct      # 回測誤差是以「全年 EPS」為分母算的，所以區間也以全年 EPS 為基準
            e["range_pct"] = round(pct, 3)
            e["range"] = [round(f - band, 2), round(f + band, 2)]
    SITE.mkdir(parents=True, exist_ok=True)
    doc = {"as_of": date.today().isoformat(), "target_year": Y,
           "sources_date": {"monthly_revenue": f"{Y}-{latest_month:02d}", "quarterly": f"{int(gq['year'])}Q{int(gq['quarter'])}",
                            "price": str(px["date"].max())},
           "method": "今年預估 EPS＝已公布季 EPS＋剩餘營收×近四季淨利率（業外以近八季中位數調整）÷股數；全年營收用旺淡季係數推估並換算到財報口徑；"
                     "自由現金流＝營業現金流−資本支出（fcf_approx 為營業＋投資現金流近似值）；缺資料為 null",
           "stocks": out}
    (SITE / "stocks.json").write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":"),
                                                 default=lambda o: o.item() if hasattr(o, "item") else str(o)), encoding="utf-8")
    okn = sum(1 for s in out if s.get("eps", {}).get("forecast") is not None)
    print(f"stocks.json：{len(out)} 家，有預估 EPS {okn} 家，{(SITE / 'stocks.json').stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
