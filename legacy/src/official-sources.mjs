import { buildThreeScenarios, calculateConfidence, calculateRoe, industryGate, normalizeIndustry } from "../calc/model.mjs";

const ENDPOINTS = {
  twse: {
    basic: "https://openapi.twse.com.tw/v1/opendata/t187ap03_L",
    revenue: "https://openapi.twse.com.tw/v1/opendata/t187ap05_L",
    income: "https://openapi.twse.com.tw/v1/opendata/t187ap06_L_ci",
    balance: "https://openapi.twse.com.tw/v1/opendata/t187ap07_L_ci",
    price: "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
    dividend: "https://openapi.twse.com.tw/v1/opendata/t187ap45_L"
  },
  tpex: {
    basic: "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O",
    revenue: "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O",
    income: "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap06_O_ci",
    balance: "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap07_O_ci",
    price: "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes",
    dividend: "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap39_O"
  }
};

const cache = new Map();
const num = (value) => {
  if (value === "" || value === null || value === undefined) return null;
  const parsed = Number(String(value).replaceAll(",", ""));
  return Number.isFinite(parsed) ? parsed : null;
};
const millionFromThousands = (value) => {
  const parsed = num(value);
  return parsed === null ? null : parsed / 1000;
};

async function fetchJson(url) {
  const previous = cache.get(url);
  if (previous && Date.now() - previous.time < 15 * 60 * 1000) return previous.data;
  let lastError;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      const response = await fetch(url, { headers: { accept: "application/json" } });
      if (!response.ok) throw new Error(`資料來源回應 ${response.status}`);
      const data = await response.json();
      cache.set(url, { time: Date.now(), data });
      return data;
    } catch (error) {
      lastError = error;
      if (attempt < 2) await new Promise((resolve) => setTimeout(resolve, 400 * (attempt + 1)));
    }
  }
  throw new Error(`官方資料暫時無法取得：${lastError?.message ?? "未知錯誤"}`);
}

const codeOf = (row) => String(row?.["公司代號"] ?? row?.SecuritiesCompanyCode ?? row?.Code ?? "").trim();
const nameOf = (row) => String(row?.["公司簡稱"] ?? row?.CompanyAbbreviation ?? row?.["公司名稱"] ?? row?.CompanyName ?? row?.Name ?? "").trim();

async function loadMarket(market) {
  const entries = await Promise.all(Object.entries(ENDPOINTS[market]).map(async ([key, url]) => [key, await fetchJson(url)]));
  return Object.fromEntries(entries);
}

function findCompany(data, query) {
  const exact = data.basic.find((row) => codeOf(row) === query || nameOf(row) === query);
  if (exact) return exact;
  return data.basic.find((row) => codeOf(row).includes(query) || nameOf(row).includes(query));
}

function normalize(market, datasets, basic) {
  const code = codeOf(basic);
  const revenue = datasets.revenue.find((row) => codeOf(row) === code);
  const income = datasets.income.find((row) => codeOf(row) === code);
  const balance = datasets.balance.find((row) => codeOf(row) === code);
  const price = datasets.price.find((row) => codeOf(row) === code);
  const dividends = datasets.dividend.filter((row) => codeOf(row) === code);
  const yearMonth = String(revenue?.["資料年月"] ?? "");
  const publishedMonths = num(yearMonth.slice(-2)) ?? 0;
  const publishedQuarters = num(income?.["季別"] ?? income?.Season) ?? 0;
  const issueShares = num(basic?.["已發行普通股數或TDR原股發行股數"] ?? basic?.IssueShares);
  const treasuryShares = num(balance?.["母公司暨子公司所持有之母公司庫藏股股數（單位：股）"] ?? balance?.["母公司暨子公司所持有之母公司庫藏股股數"]);
  const weightedShares = issueShares === null ? null : (issueShares - (treasuryShares ?? 0)) / 1_000_000;
  const industry = normalizeIndustry(revenue?.["產業別"] ?? basic?.["產業別"] ?? basic?.SecuritiesIndustryCode ?? "");
  const reportedParentEquity = millionFromThousands(balance?.["歸屬於母公司業主之權益合計"]);
  const totalEquity = millionFromThousands(balance?.["權益總計"]);
  const nciEquity = millionFromThousands(balance?.["非控制權益"]);
  const parentEquity = reportedParentEquity ?? (totalEquity === null ? null : totalEquity - (nciEquity ?? 0));
  const netIncome = millionFromThousands(income?.["本期淨利（淨損）"]);
  const nci = millionFromThousands(income?.["淨利（淨損）歸屬於非控制權益"]);
  const reportedParentNi = millionFromThousands(income?.["淨利（淨損）歸屬於母公司業主"]);
  // 部分沒有非控制權益的公司會把「歸屬母公司」與「非控制權益」都留白；此時本期淨利即為歸屬母公司淨利。
  const parentNi = reportedParentNi ?? (netIncome === null ? null : netIncome - (nci ?? 0));
  const normalized = {
    company: {
      code,
      name: nameOf(basic),
      fullName: String(basic?.["公司名稱"] ?? basic?.CompanyName ?? "").trim(),
      market: market === "twse" ? "上市" : "上櫃",
      industry,
      website: basic?.["網址"] ?? basic?.WebAddress ?? "",
      issueShares: issueShares === null ? null : issueShares / 1_000_000,
      treasuryShares: treasuryShares === null ? null : treasuryShares / 1_000_000,
      weightedShares
    },
    latestRevenue: revenue ? {
      period: yearMonth,
      currentMonth: millionFromThousands(revenue["營業收入-當月營收"]),
      ytd: millionFromThousands(revenue["累計營業收入-當月累計營收"]),
      yoy: num(revenue["營業收入-去年同月增減(%)"]),
      ytdYoy: num(revenue["累計營業收入-前期比較增減(%)"]),
      note: revenue["備註"] ?? ""
    } : null,
    latestFinancials: income && balance ? {
      year: income["年度"] ?? income.Year,
      quarter: publishedQuarters,
      revenue: millionFromThousands(income["營業收入"]),
      grossProfit: millionFromThousands(income["營業毛利（毛損）淨額"] ?? income["營業毛利（毛損）"]),
      operatingExpense: millionFromThousands(income["營業費用"]),
      operatingProfit: millionFromThousands(income["營業利益（損失）"]),
      nonOperating: millionFromThousands(income["營業外收入及支出"]),
      pretaxIncome: millionFromThousands(income["稅前淨利（淨損）"]),
      tax: millionFromThousands(income["所得稅費用（利益）"]),
      netIncome,
      parentNetIncome: parentNi,
      nci: nci ?? 0,
      reportedEps: num(income["基本每股盈餘（元）"]),
      parentEquity
    } : null,
    price: price ? {
      date: price.Date,
      close: num(price.ClosingPrice ?? price.Close)
    } : null,
    dividends: dividends.slice(0, 12).map((row) => ({
      year: row["股利年度"],
      period: row["股利所屬年(季)度"] ?? row["期別"],
      cashDividend: num(row["股東配發-盈餘分配之現金股利(元/股)"] ?? row["股東配發內容-盈餘分配之現金股利(元/股)"]),
      capitalCashDividend: num(row["股東配發-資本公積發放之現金(元/股)"] ?? row["股東配發內容-法定盈餘公積、資本公積發放之現金(元/股)"]),
      stockDividend: num(row["股東配發-盈餘轉增資配股(元/股)"] ?? row["股東配發內容-盈餘轉增資配股(元/股)"])
    })),
    publishedMonths,
    publishedQuarters,
    sources: Object.entries(ENDPOINTS[market]).map(([type, url]) => ({ type, url }))
  };
  return normalized;
}

function enrich(normalized) {
  const gate = industryGate(normalized.company.industry);
  if (!gate.supported) return { ...normalized, gate, forecast: null, confidence: null, gaps: ["此產業需使用專用模型"] };
  const f = normalized.latestFinancials;
  const r = normalized.latestRevenue;
  if (!f || !r) return { ...normalized, gate, forecast: null, confidence: null, gaps: ["缺少最新月營收或財務報表"] };
  const forecast = buildThreeScenarios({
    publishedMonths: normalized.publishedMonths,
    publishedQuarters: normalized.publishedQuarters,
    weightedShares: normalized.company.weightedShares,
    actualRevenue: r.ytd,
    statementRevenue: f.revenue,
    actualGrossProfit: f.grossProfit,
    actualOperatingExpense: f.operatingExpense,
    actualNonOperating: f.nonOperating,
    actualPretaxIncome: f.pretaxIncome,
    actualTax: f.tax,
    actualNetIncome: f.netIncome,
    actualParentNetIncome: f.parentNetIncome,
    actualNci: f.nci
  });
  const base = forecast.available ? forecast.results.base : null;
  const confidence = calculateConfidence({
    publishedMonths: normalized.publishedMonths,
    publishedQuarters: normalized.publishedQuarters,
    realizedProfitRatio: base?.realizedProfitRatio,
    marginStability: 25,
    workingCapitalConsistency: 25,
    cashFlowQuality: 25,
    oneOffRisk: 40,
    dilutionRisk: normalized.company.treasuryShares === null ? 25 : 60,
    seasonalityRisk: 25
  });
  if (forecast.available && f.parentEquity) {
    for (const scenario of Object.values(forecast.results)) {
      const opening = f.parentEquity - (f.parentNetIncome ?? 0);
      const closing = f.parentEquity + (scenario.parentNetIncome - (f.parentNetIncome ?? 0));
      scenario.roe = calculateRoe({ parentNetIncome: scenario.parentNetIncome, openingEquity: opening, closingEquity: closing });
      scenario.earningsYield = normalized.price?.close > 0 ? scenario.eps / normalized.price.close : null;
      scenario.cashDividend = null;
      scenario.payoutRatio = null;
      scenario.dividendYield = null;
    }
  }
  return {
    ...normalized,
    gate,
    forecast,
    confidence,
    gaps: [
      "尚未建立 24–36 個月月營收歷史倉儲",
      "缺少 3–5 年 EPS、自由現金流及可比配息率，股利預估暫不出值",
      "現金增資、CB、認股權與其他稀釋事件尚待結構化",
      "ROE 暫估未納入年度內股利、增資與其他權益事件的精確時點"
    ]
  };
}

export async function getCompany(query) {
  const normalizedQuery = String(query ?? "").trim();
  if (!normalizedQuery) throw new Error("請輸入股票代號或公司名稱");
  for (const market of ["twse", "tpex"]) {
    const datasets = await loadMarket(market);
    const basic = findCompany(datasets, normalizedQuery);
    if (basic) return enrich(normalize(market, datasets, basic));
  }
  throw new Error("查無上市或上櫃公司，請確認股票代號或名稱");
}
