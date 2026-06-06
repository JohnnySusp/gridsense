CREATE TABLE IF NOT EXISTS accounts (
  premise_id TEXT PRIMARY KEY,
  consumer_name TEXT NOT NULL,
  tariff_class TEXT NOT NULL,
  current_balance NUMERIC(12,2) NOT NULL DEFAULT 0,
  tariff_rules JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_accounts_tariff_rules_gin
ON accounts USING GIN (tariff_rules);

CREATE TABLE IF NOT EXISTS invoices (
  invoice_id UUID PRIMARY KEY,
  premise_id TEXT NOT NULL REFERENCES accounts(premise_id),
  billing_month DATE NOT NULL,
  kwh NUMERIC(12,3) NOT NULL,
  amount NUMERIC(12,2) NOT NULL,
  line_items JSONB NOT NULL DEFAULT '[]'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (premise_id, billing_month)
);
