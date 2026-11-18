PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS source_documents (
  id INTEGER PRIMARY KEY,
  stock_code TEXT NOT NULL,
  report_year INTEGER NOT NULL,
  quarter INTEGER NOT NULL CHECK (quarter BETWEEN 1 AND 4),
  report_type TEXT NOT NULL,
  source_url TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  raw_path TEXT NOT NULL,
  parser_version TEXT NOT NULL,
  UNIQUE (stock_code, report_year, quarter, report_type, sha256)
);

CREATE TABLE IF NOT EXISTS xbrl_contexts (
  document_id INTEGER NOT NULL REFERENCES source_documents(id) ON DELETE CASCADE,
  context_id TEXT NOT NULL,
  start_date TEXT,
  end_date TEXT,
  instant_date TEXT,
  has_dimensions INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (document_id, context_id)
);

CREATE TABLE IF NOT EXISTS xbrl_facts (
  id INTEGER PRIMARY KEY,
  document_id INTEGER NOT NULL REFERENCES source_documents(id) ON DELETE CASCADE,
  concept TEXT NOT NULL,
  local_name TEXT NOT NULL,
  context_id TEXT NOT NULL,
  unit_ref TEXT,
  decimals_text TEXT,
  scale_value INTEGER NOT NULL DEFAULT 0,
  language TEXT,
  numeric_value REAL,
  text_value TEXT,
  FOREIGN KEY (document_id, context_id) REFERENCES xbrl_contexts(document_id, context_id)
);

CREATE TABLE IF NOT EXISTS quarterly_financials (
  id INTEGER PRIMARY KEY,
  stock_code TEXT NOT NULL,
  report_year INTEGER NOT NULL,
  quarter INTEGER NOT NULL CHECK (quarter BETWEEN 1 AND 4),
  basis TEXT NOT NULL CHECK (basis IN ('cumulative', 'single')),
  revenue REAL,
  gross_profit REAL,
  operating_expense REAL,
  operating_profit REAL,
  non_operating REAL,
  pretax_income REAL,
  income_tax REAL,
  net_income REAL,
  parent_net_income REAL,
  nci REAL,
  basic_eps REAL,
  diluted_eps REAL,
  parent_equity REAL,
  accounts_receivable REAL,
  inventories REAL,
  accounts_payable REAL,
  operating_cash_flow REAL,
  capex REAL,
  free_cash_flow REAL,
  source_document_id INTEGER NOT NULL REFERENCES source_documents(id),
  quality_status TEXT NOT NULL,
  quality_notes TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (stock_code, report_year, quarter, basis)
);

CREATE TABLE IF NOT EXISTS monthly_revenue_documents (
  id INTEGER PRIMARY KEY,
  stock_code TEXT NOT NULL,
  revenue_year INTEGER NOT NULL,
  revenue_month INTEGER NOT NULL CHECK (revenue_month BETWEEN 1 AND 12),
  source_url TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  raw_path TEXT NOT NULL,
  parser_version TEXT NOT NULL,
  UNIQUE (stock_code, revenue_year, revenue_month, sha256)
);

CREATE TABLE IF NOT EXISTS monthly_revenues (
  id INTEGER PRIMARY KEY,
  stock_code TEXT NOT NULL,
  revenue_year INTEGER NOT NULL,
  revenue_month INTEGER NOT NULL CHECK (revenue_month BETWEEN 1 AND 12),
  current_month_revenue REAL,
  prior_year_month_revenue REAL,
  month_change_amount REAL,
  yoy_percent REAL,
  year_to_date_revenue REAL,
  prior_year_to_date_revenue REAL,
  ytd_change_amount REAL,
  ytd_yoy_percent REAL,
  note TEXT NOT NULL DEFAULT '',
  currency TEXT NOT NULL DEFAULT 'TWD',
  source_unit TEXT NOT NULL DEFAULT 'thousand',
  source_document_id INTEGER NOT NULL REFERENCES monthly_revenue_documents(id),
  quality_status TEXT NOT NULL,
  quality_notes TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (stock_code, revenue_year, revenue_month)
);

CREATE TABLE IF NOT EXISTS dividend_documents (
  id INTEGER PRIMARY KEY,
  stock_code TEXT NOT NULL,
  start_year INTEGER NOT NULL,
  end_year INTEGER NOT NULL,
  source_url TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  raw_path TEXT NOT NULL,
  parser_version TEXT NOT NULL,
  UNIQUE (stock_code, start_year, end_year, sha256)
);

CREATE TABLE IF NOT EXISTS dividend_declarations (
  id INTEGER PRIMARY KEY,
  stock_code TEXT NOT NULL,
  dividend_year INTEGER NOT NULL,
  period_label TEXT NOT NULL,
  quarter INTEGER,
  issue_sequence TEXT NOT NULL DEFAULT '',
  decision_status TEXT NOT NULL DEFAULT '',
  decision_date TEXT,
  shareholder_meeting_date TEXT,
  period_net_income REAL,
  cash_dividend_earnings REAL,
  cash_dividend_reserves REAL,
  total_cash_dividend REAL,
  stock_dividend_earnings REAL,
  stock_dividend_reserves REAL,
  total_stock_dividend REAL,
  total_cash_amount REAL,
  stock_shares REAL,
  charter_text TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  source_document_id INTEGER NOT NULL REFERENCES dividend_documents(id),
  quality_status TEXT NOT NULL,
  quality_notes TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (stock_code, dividend_year, period_label, issue_sequence)
);

CREATE TABLE IF NOT EXISTS annual_fundamentals (
  id INTEGER PRIMARY KEY,
  stock_code TEXT NOT NULL,
  fiscal_year INTEGER NOT NULL,
  basic_eps REAL,
  diluted_eps REAL,
  parent_net_income REAL,
  operating_cash_flow REAL,
  capex REAL,
  free_cash_flow REAL,
  implied_average_shares REAL,
  implied_diluted_shares REAL,
  share_growth_rate REAL,
  source_document_id INTEGER REFERENCES source_documents(id),
  quality_status TEXT NOT NULL,
  quality_notes TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (stock_code, fiscal_year)
);

CREATE TABLE IF NOT EXISTS dilution_documents (
  id INTEGER PRIMARY KEY,
  stock_code TEXT NOT NULL,
  source_type TEXT NOT NULL,
  source_url TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  raw_path TEXT NOT NULL,
  parser_version TEXT NOT NULL,
  UNIQUE (stock_code, source_type, sha256)
);

CREATE TABLE IF NOT EXISTS dilution_events (
  id INTEGER PRIMARY KEY,
  stock_code TEXT NOT NULL,
  event_key TEXT NOT NULL,
  event_type TEXT NOT NULL,
  event_date TEXT,
  effective_date TEXT,
  description TEXT NOT NULL,
  currency TEXT,
  issue_amount REAL,
  outstanding_amount REAL,
  conversion_price REAL,
  potential_shares REAL,
  dilution_percent REAL,
  status TEXT NOT NULL DEFAULT '',
  source_url TEXT NOT NULL,
  source_document_id INTEGER REFERENCES dilution_documents(id),
  quality_status TEXT NOT NULL,
  quality_notes TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (stock_code, event_key)
);

CREATE TABLE IF NOT EXISTS forecast_snapshots (
  id INTEGER PRIMARY KEY,
  stock_code TEXT NOT NULL,
  target_year INTEGER NOT NULL,
  as_of_date TEXT NOT NULL,
  captured_at TEXT NOT NULL,
  model_version TEXT NOT NULL,
  published_months INTEGER NOT NULL,
  published_quarters INTEGER NOT NULL,
  price REAL,
  conservative_eps REAL,
  base_eps REAL,
  optimistic_eps REAL,
  conservative_roe REAL,
  base_roe REAL,
  optimistic_roe REAL,
  conservative_cash_dividend REAL,
  base_cash_dividend REAL,
  optimistic_cash_dividend REAL,
  base_stock_dividend REAL,
  base_payout_ratio REAL,
  base_dividend_yield REAL,
  confidence_score REAL,
  confidence_level TEXT,
  realized_eps REAL,
  remaining_eps REAL,
  realized_profit_ratio REAL,
  payload_json TEXT NOT NULL,
  UNIQUE (stock_code, target_year, as_of_date, model_version)
);

CREATE INDEX IF NOT EXISTS idx_source_documents_company_period
ON source_documents(stock_code, report_year DESC, quarter DESC);

CREATE INDEX IF NOT EXISTS idx_xbrl_facts_document_local_name
ON xbrl_facts(document_id, local_name);

CREATE INDEX IF NOT EXISTS idx_quarterly_financials_company_basis_period
ON quarterly_financials(stock_code, basis, report_year DESC, quarter DESC);

CREATE INDEX IF NOT EXISTS idx_monthly_revenue_documents_company_period
ON monthly_revenue_documents(stock_code, revenue_year DESC, revenue_month DESC);

CREATE INDEX IF NOT EXISTS idx_monthly_revenues_company_period
ON monthly_revenues(stock_code, revenue_year DESC, revenue_month DESC);

CREATE INDEX IF NOT EXISTS idx_dividend_declarations_company_period
ON dividend_declarations(stock_code, dividend_year DESC, quarter DESC);

CREATE INDEX IF NOT EXISTS idx_annual_fundamentals_company_year
ON annual_fundamentals(stock_code, fiscal_year DESC);

CREATE INDEX IF NOT EXISTS idx_dilution_events_company_date
ON dilution_events(stock_code, event_date DESC);

CREATE INDEX IF NOT EXISTS idx_forecast_snapshots_company_year_date
ON forecast_snapshots(stock_code, target_year, as_of_date DESC);

CREATE TABLE IF NOT EXISTS update_jobs (
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  stock_codes_json TEXT NOT NULL,
  steps_json TEXT NOT NULL,
  total_tasks INTEGER NOT NULL,
  completed_tasks INTEGER NOT NULL DEFAULT 0,
  success_count INTEGER NOT NULL DEFAULT 0,
  failure_count INTEGER NOT NULL DEFAULT 0,
  current_task TEXT,
  messages_json TEXT NOT NULL DEFAULT '[]',
  started_at TEXT NOT NULL,
  completed_at TEXT,
  duration_seconds REAL,
  retry_of TEXT,
  requested_action TEXT
);

CREATE INDEX IF NOT EXISTS idx_update_jobs_started_at
ON update_jobs(started_at DESC);

CREATE TABLE IF NOT EXISTS update_job_tasks (
  job_id TEXT NOT NULL,
  task_index INTEGER NOT NULL,
  stock_code TEXT NOT NULL,
  step TEXT NOT NULL,
  label TEXT NOT NULL,
  script TEXT NOT NULL,
  args_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  error TEXT,
  started_at TEXT,
  completed_at TEXT,
  PRIMARY KEY (job_id, task_index),
  FOREIGN KEY (job_id) REFERENCES update_jobs(id)
);

CREATE INDEX IF NOT EXISTS idx_update_job_tasks_status
ON update_job_tasks(job_id, status, task_index);

CREATE TABLE IF NOT EXISTS update_schedule (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  enabled INTEGER NOT NULL DEFAULT 0,
  stock_codes_json TEXT NOT NULL,
  steps_json TEXT NOT NULL,
  frequency TEXT NOT NULL,
  weekday INTEGER NOT NULL,
  run_time TEXT NOT NULL,
  timezone TEXT NOT NULL DEFAULT 'Asia/Taipei',
  last_run_date TEXT,
  next_run_at TEXT,
  last_job_id TEXT,
  last_started_at TEXT,
  last_completed_at TEXT,
  last_status TEXT,
  consecutive_failures INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  max_retries INTEGER NOT NULL DEFAULT 2,
  retry_delay_minutes INTEGER NOT NULL DEFAULT 15,
  current_retry_count INTEGER NOT NULL DEFAULT 0,
  retry_at TEXT,
  updated_at TEXT NOT NULL
);

PRAGMA optimize;
