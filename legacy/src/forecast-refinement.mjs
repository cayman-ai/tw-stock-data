import { buildThreeScenarios } from "../calc/model.mjs";

function median(values) {
  const valid = values.filter(Number.isFinite).sort((a, b) => a - b);
  if (!valid.length) return null;
  const middle = Math.floor(valid.length / 2);
  return valid.length % 2 ? valid[middle] : (valid[middle - 1] + valid[middle]) / 2;
}

export function estimateSeasonalAnnualRevenue(rows, actualYtd, publishedMonths) {
  const months = Number(publishedMonths);
  if (!Number.isFinite(actualYtd) || actualYtd <= 0 || !Number.isInteger(months) || months < 1 || months >= 12) {
    return { available: false, estimate: null, reason: "已公布月份不足以計算旺淡季比例", comparableYears: 0, factors: [] };
  }
  const years = new Map();
  for (const row of rows ?? []) {
    if (!Number.isFinite(row.revenueYear) || !Number.isFinite(row.revenueMonth) || !Number.isFinite(row.currentMonthRevenue)) continue;
    if (!years.has(row.revenueYear)) years.set(row.revenueYear, []);
    years.get(row.revenueYear).push(row);
  }
  const factors = [];
  for (const [year, yearRows] of years) {
    if (yearRows.length !== 12) continue;
    const annual = yearRows.reduce((sum, row) => sum + row.currentMonthRevenue, 0);
    const sameMonths = yearRows.filter((row) => row.revenueMonth <= months);
    if (sameMonths.length !== months) continue;
    const ytd = sameMonths.reduce((sum, row) => sum + row.currentMonthRevenue, 0);
    if (annual > 0 && ytd > 0) factors.push({ year, factor: annual / ytd });
  }
  const factor = median(factors.map((item) => item.factor));
  if (factor === null || factors.length < 2) {
    return { available: false, estimate: null, reason: "至少需要兩個完整年度才能套用旺淡季比例", comparableYears: factors.length, factors };
  }
  return { available: true, estimate: actualYtd * factor, comparableYears: factors.length, factor, factors };
}

export function estimateRecurringQuarterlyNonOperating(rows) {
  const values = (rows ?? []).slice(0, 8).map((row) => row.nonOperating).filter(Number.isFinite);
  const estimate = median(values);
  return {
    available: values.length >= 4,
    estimate: values.length >= 4 ? estimate : null,
    observedQuarters: values.length,
    method: "近八季單季非本業損益中位數"
  };
}

export function refineForecast(companyData) {
  if (!companyData.forecast?.available || !companyData.latestRevenue || !companyData.latestFinancials) return companyData;
  const seasonal = estimateSeasonalAnnualRevenue(
    companyData.revenueHistory?.rows,
    companyData.latestRevenue.ytd,
    companyData.publishedMonths
  );
  const recurringNonOperating = estimateRecurringQuarterlyNonOperating(companyData.history?.rows);
  const f = companyData.latestFinancials;
  const forecast = buildThreeScenarios({
    publishedMonths: companyData.publishedMonths,
    publishedQuarters: companyData.publishedQuarters,
    weightedShares: companyData.company.weightedShares,
    actualRevenue: companyData.latestRevenue.ytd,
    statementRevenue: f.revenue,
    actualGrossProfit: f.grossProfit,
    actualOperatingExpense: f.operatingExpense,
    actualNonOperating: f.nonOperating,
    actualPretaxIncome: f.pretaxIncome,
    actualTax: f.tax,
    actualNetIncome: f.netIncome,
    actualParentNetIncome: f.parentNetIncome,
    actualNci: f.nci,
    annualRevenueEstimate: seasonal.available ? seasonal.estimate : null,
    recurringQuarterlyNonOperating: recurringNonOperating.available ? recurringNonOperating.estimate : null
  });
  if (!forecast.available) return companyData;
  forecast.trace = {
    revenue: seasonal.available
      ? `採 ${seasonal.comparableYears} 個完整年度的同期營收占比中位數推估剩餘月份。`
      : `歷史旺淡季資料不足，改用已公布 ${companyData.publishedMonths} 個月平均年化。`,
    seasonality: seasonal,
    nonOperating: recurringNonOperating.available
      ? `剩餘季度非本業損益採近 ${recurringNonOperating.observedQuarters} 季單季中位數，降低單次鉅額收益或損失被重複外推的風險。`
      : "可比季度不足，剩餘非本業損益暫用本年度已公布季度平均。",
    recurringNonOperating
  };
  return { ...companyData, forecast };
}
