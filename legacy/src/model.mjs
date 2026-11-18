const finite = (value) => {
  if (value === null || value === undefined || value === "") return null;
  return Number.isFinite(Number(value)) ? Number(value) : null;
};
const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

export const roundOne = (value) => {
  const number = finite(value);
  return number === null ? null : Math.round((number + Number.EPSILON) * 10) / 10;
};

export function normalizeIndustry(industry = "") {
  const text = String(industry).trim();
  return /^0*17(?:\.0+)?$/.test(text) ? "金融保險業" : text;
}

export function industryGate(industry = "") {
  const text = normalizeIndustry(industry);
  if (/金融保險/.test(text)) return { supported: false, model: "金融保險業專用模型" };
  if (/銀行|金融|金控/.test(text)) return { supported: false, model: "銀行業專用模型" };
  if (/保險/.test(text)) return { supported: false, model: "保險業專用模型" };
  if (/營建|建材營造/.test(text)) return { supported: false, model: "營建業專用模型" };
  return { supported: true, model: "一般產業模型" };
}

export function calculateEpsScenario(input) {
  const required = [
    "actualRevenue", "actualGrossProfit", "actualOperatingExpense",
    "actualNonOperating", "actualTax", "actualParentNetIncome",
    "annualRevenue", "grossMargin", "operatingExpenseRate",
    "remainingNonOperating", "effectiveTaxRate", "nciRate", "weightedShares"
  ];
  for (const key of required) {
    if (finite(input[key]) === null) return { available: false, reason: `缺少 ${key}` };
  }

  const remainingRevenue = Math.max(0, input.annualRevenue - input.actualRevenue);
  const forecastGrossProfit = remainingRevenue * input.grossMargin;
  const annualGrossProfit = input.actualGrossProfit + forecastGrossProfit;
  const forecastOperatingExpense = remainingRevenue * input.operatingExpenseRate;
  const annualOperatingExpense = input.actualOperatingExpense + forecastOperatingExpense;
  const annualOperatingProfit = annualGrossProfit - annualOperatingExpense;
  const annualNonOperating = input.actualNonOperating + input.remainingNonOperating;
  const pretaxIncome = annualOperatingProfit + annualNonOperating;
  const incomeTax = pretaxIncome > 0 ? pretaxIncome * input.effectiveTaxRate : 0;
  const netIncome = pretaxIncome - incomeTax;
  const actualNetIncome = input.actualParentNetIncome + (finite(input.actualNci) ?? 0);
  const remainingNetIncome = netIncome - actualNetIncome;
  const forecastNci = Math.max(0, remainingNetIncome) * input.nciRate;
  const parentNetIncome = netIncome - (finite(input.actualNci) ?? 0) - forecastNci;
  const eps = input.weightedShares > 0 ? parentNetIncome / input.weightedShares : null;
  const realizedEps = input.weightedShares > 0 ? input.actualParentNetIncome / input.weightedShares : null;
  const remainingEps = eps === null || realizedEps === null ? null : eps - realizedEps;
  const realizedProfitRatio = parentNetIncome !== 0 ? input.actualParentNetIncome / parentNetIncome : null;

  return {
    available: eps !== null,
    annualRevenue: input.annualRevenue,
    grossProfit: annualGrossProfit,
    operatingExpense: annualOperatingExpense,
    operatingProfit: annualOperatingProfit,
    nonOperating: annualNonOperating,
    pretaxIncome,
    incomeTax,
    netIncome,
    parentNetIncome,
    weightedShares: input.weightedShares,
    eps,
    realizedEps,
    remainingEps,
    realizedProfitRatio
  };
}

export function calculateRoe({ parentNetIncome, openingEquity, closingEquity }) {
  const profit = finite(parentNetIncome);
  const opening = finite(openingEquity);
  const closing = finite(closingEquity);
  if (profit === null || opening === null || closing === null || opening + closing <= 0) return null;
  return profit / ((opening + closing) / 2);
}

export function calculateDividend({ eps, history = [], fcfPerShare = null, payoutOverride = null }) {
  const forecastEps = finite(eps);
  if (forecastEps === null || forecastEps <= 0) return { available: false, reason: "EPS 非正數或資料不足" };
  const validPayouts = history
    .map((row) => finite(row.payoutRatio))
    .filter((value) => value !== null && value >= 0 && value <= 1.5);
  const override = finite(payoutOverride);
  if (validPayouts.length < 3 && override === null) {
    return { available: false, reason: `至少需要 3 年可比配息率，目前只有 ${validPayouts.length} 年；未套用固定配息率` };
  }
  const sorted = [...validPayouts].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  const median = sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
  const payoutRatio = override ?? median;
  let cashDividend = forecastEps * payoutRatio;
  const fcf = finite(fcfPerShare);
  if (fcf !== null && fcf >= 0) cashDividend = Math.min(cashDividend, fcf);
  return { available: true, payoutRatio, cashDividend };
}

export function calculateConfidence(input) {
  const months = clamp(finite(input.publishedMonths) ?? 0, 0, 12);
  const quarters = clamp(finite(input.publishedQuarters) ?? 0, 0, 4);
  const realized = clamp(finite(input.realizedProfitRatio) ?? 0, 0, 1);
  const maturity = ((months / 12) * 60 + (quarters / 4) * 40);
  const coverage = realized * 100;
  const optional = (value) => finite(value) ?? 25;
  const scores = {
    dataMaturity: maturity,
    realizedCoverage: coverage,
    marginStability: optional(input.marginStability),
    workingCapitalConsistency: optional(input.workingCapitalConsistency),
    cashFlowQuality: optional(input.cashFlowQuality),
    oneOffRisk: optional(input.oneOffRisk),
    dilutionRisk: optional(input.dilutionRisk),
    seasonalityRisk: optional(input.seasonalityRisk)
  };
  const weights = {
    dataMaturity: 0.25,
    realizedCoverage: 0.15,
    marginStability: 0.15,
    workingCapitalConsistency: 0.10,
    cashFlowQuality: 0.10,
    oneOffRisk: 0.10,
    dilutionRisk: 0.10,
    seasonalityRisk: 0.05
  };
  const total = Object.keys(scores).reduce((sum, key) => sum + scores[key] * weights[key], 0);
  const explanation = (score, good, medium, weak) => score >= 80 ? good : score >= 55 ? medium : weak;
  const explanations = {
    dataMaturity: explanation(scores.dataMaturity, "年度資料大多已公布", "已有部分實際資料", "實際公布資料仍少"),
    realizedCoverage: explanation(scores.realizedCoverage, "全年獲利多數已實現", "已有一定比例獲利實現", "全年獲利仍多半仰賴預估"),
    marginStability: explanation(scores.marginStability, "近期毛利率相對穩定", "毛利率有一些波動", "毛利率波動較大或資料不足"),
    workingCapitalConsistency: explanation(scores.workingCapitalConsistency, "應收帳款與存貨變化相對穩定", "營運資金有一些波動", "營運資金波動較大或資料不足"),
    cashFlowQuality: explanation(scores.cashFlowQuality, "營業現金流能支持帳面獲利", "現金流與獲利大致相符", "現金流支持度較弱或資料不足"),
    oneOffRisk: explanation(scores.oneOffRisk, "非本業損益占比較低", "非本業損益有一定影響", "一次性或非本業損益影響較大"),
    dilutionRisk: explanation(scores.dilutionRisk, "目前辨識出的股權稀釋風險較低", "存在部分股數增加風險", "股權稀釋風險較高或資料不足"),
    seasonalityRisk: explanation(scores.seasonalityRisk, "歷史旺淡季影響較穩定", "剩餘月份仍受旺淡季影響", "旺淡季差異較大或資料不足")
  };
  return {
    total,
    level: total >= 80 ? "高" : total >= 60 ? "中" : "低",
    scores,
    explanations,
    note: "缺少歷史資料的子項以 25 分計入，可信度不是投資評等。"
  };
}

export function buildThreeScenarios(actual) {
  const months = clamp(finite(actual.publishedMonths) ?? 0, 1, 12);
  const quarter = clamp(finite(actual.publishedQuarters) ?? 0, 1, 4);
  const shares = finite(actual.weightedShares);
  const actualRevenue = finite(actual.actualRevenue);
  const statementRevenue = finite(actual.statementRevenue);
  if ([shares, actualRevenue, statementRevenue].some((value) => value === null || value <= 0)) {
    return { available: false, reason: "官方資料不足，無法建立三情境" };
  }
  const seasonalAnnualRevenue = finite(actual.annualRevenueEstimate);
  const baseAnnualRevenue = seasonalAnnualRevenue !== null && seasonalAnnualRevenue >= actualRevenue
    ? seasonalAnnualRevenue : actualRevenue / months * 12;
  const grossMargin = clamp(actual.actualGrossProfit / statementRevenue, -1, 1);
  const opexRate = clamp(actual.actualOperatingExpense / statementRevenue, 0, 1);
  const effectiveTaxRate = actual.actualPretaxIncome > 0
    ? clamp(actual.actualTax / actual.actualPretaxIncome, 0, 0.5)
    : 0.2;
  const nciRate = actual.actualNetIncome > 0
    ? clamp((actual.actualNci ?? 0) / actual.actualNetIncome, 0, 0.5)
    : 0;
  const remainingQuarters = Math.max(0, 4 - quarter);
  const recurringQuarterlyNonOperating = finite(actual.recurringQuarterlyNonOperating);
  const remainingNonOperatingBase = recurringQuarterlyNonOperating !== null
    ? recurringQuarterlyNonOperating * remainingQuarters
    : actual.actualNonOperating * remainingQuarters / quarter;
  const definitions = [
    { key: "conservative", label: "保守", revenueFactor: 0.92, marginDelta: -0.02, opexDelta: 0.01, nonOpFactor: 0.7 },
    { key: "base", label: "基準", revenueFactor: 1.00, marginDelta: 0, opexDelta: 0, nonOpFactor: 1.0 },
    { key: "optimistic", label: "樂觀", revenueFactor: 1.08, marginDelta: 0.02, opexDelta: -0.01, nonOpFactor: 1.2 }
  ];
  const results = {};
  for (const scenario of definitions) {
    const remainingBase = Math.max(0, baseAnnualRevenue - actualRevenue);
    const annualRevenue = actualRevenue + remainingBase * scenario.revenueFactor;
    results[scenario.key] = {
      label: scenario.label,
      ...calculateEpsScenario({
        actualRevenue: statementRevenue,
        actualGrossProfit: actual.actualGrossProfit,
        actualOperatingExpense: actual.actualOperatingExpense,
        actualNonOperating: actual.actualNonOperating,
        actualTax: actual.actualTax,
        actualParentNetIncome: actual.actualParentNetIncome,
        actualNci: actual.actualNci,
        annualRevenue,
        grossMargin: clamp(grossMargin + scenario.marginDelta, -1, 1),
        operatingExpenseRate: clamp(opexRate + scenario.opexDelta, 0, 1),
        remainingNonOperating: remainingNonOperatingBase * scenario.nonOpFactor,
        effectiveTaxRate,
        nciRate,
        weightedShares: shares
      })
    };
  }
  const unavailable = Object.values(results).find((scenario) => !scenario.available);
  if (unavailable) return { available: false, reason: unavailable.reason ?? "公開資料不足，暫時無法完成預估" };
  return {
    available: true,
    results,
    assumptions: {
      baseAnnualRevenue, grossMargin, opexRate, effectiveTaxRate,
      revenueMethod: seasonalAnnualRevenue !== null && seasonalAnnualRevenue >= actualRevenue ? "歷史旺淡季比例" : "已公布月份平均年化",
      remainingNonOperatingBase,
      nonOperatingMethod: recurringQuarterlyNonOperating !== null ? "近八季單季非本業損益中位數" : "本年度已公布季度平均"
    }
  };
}
