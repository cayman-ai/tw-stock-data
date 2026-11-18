"""各官方資料來源的抓取與解析。每個 parse_* 回傳 list[dict]，不做任何估算。"""
from __future__ import annotations

import json
import re

from .common import fetch, flatten, is_common_code, num, pick, read_tables

MOPS = "https://mopsov.twse.com.tw/mops/web/"
MARKETS = {"sii": "上市", "otc": "上櫃"}

# ---------- 公司基本資料 ----------
CO_URL = {"sii": "https://openapi.twse.com.tw/v1/opendata/t187ap03_L",
          "otc": "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"}
INDUSTRY = {"01": "水泥工業", "02": "食品工業", "03": "塑膠工業", "04": "紡織纖維", "05": "電機機械", "06": "電器電纜",
            "08": "玻璃陶瓷", "09": "造紙工業", "10": "鋼鐵工業", "11": "橡膠工業", "12": "汽車工業", "14": "建材營造業",
            "15": "航運業", "16": "觀光餐旅", "17": "金融保險業", "18": "貿易百貨業", "19": "綜合", "20": "其他業",
            "21": "化學工業", "22": "生技醫療業", "23": "油電燃氣業", "24": "半導體業", "25": "電腦及週邊設備業",
            "26": "光電業", "27": "通信網路業", "28": "電子零組件業", "29": "電子通路業", "30": "資訊服務業",
            "31": "其他電子業", "32": "文化創意業", "33": "農業科技業", "34": "電子商務", "35": "綠能環保",
            "36": "數位雲端", "37": "運動休閒", "38": "居家生活", "80": "管理股票", "91": "存託憑證"}


def companies(market: str) -> list[dict]:
    rows = json.loads(fetch(CO_URL[market]))
    out = []
    for r in rows:
        code = r.get("公司代號") or r.get("SecuritiesCompanyCode")
        if not is_common_code(code):
            continue
        ind = (r.get("產業別") or r.get("SecuritiesIndustryCode") or "").strip()
        par = re.search(r"([\d.]+)", r.get("普通股每股面額") or r.get("ParValueOfCommonStock") or "")
        cap = num(r.get("實收資本額") or r.get("Paidin.Capital.NTDollars"))
        pref = num(r.get("特別股") or r.get("PreferredStock.shares")) or 0
        par_v = float(par.group(1)) if par else None
        shares = (cap / par_v - pref) if cap and par_v else None
        out.append(dict(code=str(code).strip(), name=(r.get("公司簡稱") or r.get("CompanyAbbreviation") or "").strip(),
                        market=MARKETS[market], industry_code=ind, industry=INDUSTRY.get(ind, ind),
                        listed_shares=round(shares) if shares else None,
                        # 編制財務報表類型：1＝合併報表（有子公司）、2＝個體報表（無子公司）
                        report_type=str(r.get("編制財務報表類型") or r.get("PreparationOfFinancialReportType") or "").strip()))
    return out


# ---------- 月營收（每月彙總表，上市/上櫃 × 國內/外國企業） ----------
def revenue_url(market: str, year: int, month: int, foreign: int) -> str:
    return f"https://mopsov.twse.com.tw/nas/t21/{market}/t21sc03_{year - 1911}_{month}_{foreign}.html"


def monthly_revenue(market: str, year: int, month: int) -> list[dict]:
    """國內（_0）與外國企業（_1）兩張表各自檢查：任一張解析不到公司就報錯，避免整張表消失卻沒發現。"""
    out = {}
    for foreign in (0, 1):
        html = fetch(revenue_url(market, year, month, foreign))
        before = len(out)
        for t in read_tables(html):
            t = flatten(t)
            if "公司代號" not in t.columns or "當月營收" not in t.columns:
                continue
            for _, r in t.iterrows():
                code = str(r["公司代號"]).strip()
                if not is_common_code(code):
                    continue
                out[code] = dict(code=code, year=year, month=month, revenue=num(r["當月營收"]),
                                 ytd_revenue=num(r.get("當月累計營收")),
                                 last_year_ytd_revenue=num(r.get("去年累計營收")),
                                 last_year_revenue=num(r.get("去年當月營收")))
        if len(out) - before < (300 if foreign == 0 else 5):
            raise RuntimeError(f"月營收 {market} {year}-{month:02d} {'外國' if foreign else '國內'}企業表只解析到 {len(out) - before} 家，可能改版")
    return list(out.values())


# ---------- 季報彙總表（累計值，千元） ----------
def _mops_summary(kind: str, market: str, year: int, quarter: int):
    data = dict(encodeURIComponent=1, step=1, firstin=1, off=1, isQuery="Y",
                TYPEK=market, year=str(year - 1911), season=f"{quarter:02d}")
    html = fetch(MOPS + f"ajax_{kind}", data)
    rows = []
    for t in read_tables(html):
        t = flatten(t)
        if "公司代號" not in t.columns:
            continue
        for _, r in t.iterrows():
            if is_common_code(r["公司代號"]):
                rows.append(r)
    return rows


def income_statement(market: str, year: int, quarter: int) -> list[dict]:
    out = []
    for r in _mops_summary("t163sb04", market, year, quarter):
        out.append(dict(
            code=str(r["公司代號"]).strip(), year=year, quarter=quarter,
            revenue=pick(r, "營業收入", "收益", "收入"),
            gross_profit=pick(r, "營業毛利（毛損）淨額", "營業毛利（毛損）"),
            operating_expense=pick(r, "營業費用", "支出及費用"),
            operating_income=pick(r, "營業利益（損失）", "營業利益"),
            non_operating=pick(r, "營業外收入及支出", "營業外損益"),
            pretax_income=pick(r, "稅前淨利（淨損）", "繼續營業單位稅前淨利（淨損）", "繼續營業單位稅前損益"),
            income_tax=pick(r, "所得稅費用（利益）"),
            net_income=pick(r, "本期淨利（淨損）", "本期稅後淨利（淨損）"),
            parent_net_income=pick(r, "淨利（淨損）歸屬於母公司業主", "淨利（損）歸屬於母公司業主"),
            nci_net_income=pick(r, "淨利（淨損）歸屬於非控制權益", "淨利（損）歸屬於非控制權益"),
            eps=pick(r, "基本每股盈餘（元）"),
        ))
    return out


def balance_sheet(market: str, year: int, quarter: int) -> list[dict]:
    out = []
    for r in _mops_summary("t163sb05", market, year, quarter):
        out.append(dict(
            code=str(r["公司代號"]).strip(), year=year, quarter=quarter,
            total_assets=pick(r, "資產總計", "資產總額"),
            total_liabilities=pick(r, "負債總計", "負債總額"),
            parent_equity=pick(r, "歸屬於母公司業主之權益合計", "歸屬於母公司業主之權益"),
            total_equity=pick(r, "權益總計", "權益總額"),
            capital_stock=pick(r, "股本"),
            bvps=pick(r, "每股參考淨值"),
        ))
    return out


def cash_flow(market: str, year: int, quarter: int) -> list[dict]:
    out = []
    for r in _mops_summary("t163sb20", market, year, quarter):
        out.append(dict(
            code=str(r["公司代號"]).strip(), year=year, quarter=quarter,
            operating_cash_flow=pick(r, "營業活動之淨現金流入（流出）"),
            investing_cash_flow=pick(r, "投資活動之淨現金流入（流出）"),
        ))
    return out


# ---------- 股利（除權息彙總，依「股利所屬期間」歸到盈餘年度） ----------
def dividends(market: str, query_year: int) -> list[dict]:
    data = dict(encodeURIComponent=1, step=1, firstin=1, off=1, isQuery="Y",
                TYPEK=market, year=str(query_year - 1911), type1="0", qryType="1")
    html = fetch(MOPS + "ajax_t108sb27", data)
    out = []
    for t in read_tables(html):
        t = flatten(t)
        if "公司代號" not in t.columns or "股利所屬期間" not in t.columns:
            continue
        need = ["盈餘分配之股東現金股利(元/股)", "法定盈餘公積、資本公積發放之現金(元/股)",
                "盈餘轉增資配股(元/股)", "法定盈餘公積、資本公積轉增資配股(元/股)", "權利分派基準日"]
        miss = [c for c in need if c not in t.columns]
        if miss:   # 欄名改了就報錯，不能把「整欄不存在」當成 0
            raise RuntimeError(f"股利表缺少欄位：{miss}，可能官方改版")
        for _, r in t.iterrows():
            code = str(r["公司代號"]).strip()
            if not is_common_code(code):
                continue
            period = str(r["股利所屬期間"]).strip()
            m = re.match(r"(\d{2,3})年", period)
            if not m:
                continue
            cash_e = num(r.get("盈餘分配之股東現金股利(元/股)")) or 0
            cash_c = num(r.get("法定盈餘公積、資本公積發放之現金(元/股)")) or 0
            stock_e = num(r.get("盈餘轉增資配股(元/股)")) or 0
            stock_c = num(r.get("法定盈餘公積、資本公積轉增資配股(元/股)")) or 0
            out.append(dict(code=code, period=period, earnings_year=int(m.group(1)) + 1911,
                            record_date=str(r.get("權利分派基準日", "")).strip(),
                            cash_from_earnings=cash_e, cash_from_capital=cash_c,
                            stock_from_earnings=stock_e, stock_from_capital=stock_c))
    return out


# ---------- 最新股價與本益比 ----------
def prices() -> list[dict]:
    out = {}
    px = json.loads(fetch("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"))
    pe = json.loads(fetch("https://www.twse.com.tw/exchangeReport/BWIBBU_ALL?response=json"))
    for r in px:
        if is_common_code(r["Code"]):
            out[r["Code"]] = dict(code=r["Code"], date=r["Date"], close=num(r["ClosingPrice"]), pe=None, pb=None, dividend_yield=None)
    for code, _name, p, y, b in pe.get("data", []):
        if code in out:
            out[code].update(pe=num(p), dividend_yield=num(y), pb=num(b))
    px2 = json.loads(fetch("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes"))
    pe2 = json.loads(fetch("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis"))
    for r in px2:
        c = r["SecuritiesCompanyCode"]
        if is_common_code(c):
            out[c] = dict(code=c, date=r["Date"], close=num(r["Close"]), pe=None, pb=None, dividend_yield=None)
    for r in pe2:
        c = r["SecuritiesCompanyCode"]
        if c in out:
            out[c].update(pe=num(r["PriceEarningRatio"]), dividend_yield=num(r["YieldRatio"]), pb=num(r["PriceBookRatio"]))
    for v in out.values():   # 民國日期 → 西元
        d = str(v["date"])
        if len(d) == 7:
            v["date"] = f"{int(d[:3]) + 1911}-{d[3:5]}-{d[5:]}"
    return list(out.values())
