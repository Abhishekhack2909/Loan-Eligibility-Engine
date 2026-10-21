-- Loan Eligibility Engine relational model.
-- Apply with a PostgreSQL 15+ client. The migration is idempotent so it can
-- be safely used by a migration runner that retries a partially applied step.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS upload_batches (
    id UUID PRIMARY KEY,
    object_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'created'
        CHECK (status IN ('created', 'processing', 'completed', 'failed')),
    row_count INTEGER NOT NULL DEFAULT 0 CHECK (row_count >= 0),
    error_count INTEGER NOT NULL DEFAULT 0 CHECK (error_count >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    upload_batch_id UUID NOT NULL REFERENCES upload_batches(id) ON DELETE CASCADE,
    source_row_number INTEGER NOT NULL CHECK (source_row_number >= 2),
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    monthly_income NUMERIC(14, 2) NOT NULL CHECK (monthly_income >= 0),
    credit_score INTEGER NOT NULL CHECK (credit_score BETWEEN 0 AND 850),
    employment_status TEXT NOT NULL,
    age INTEGER NOT NULL CHECK (age BETWEEN 0 AND 150),
    raw_data JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT users_upload_row_unique UNIQUE (upload_batch_id, source_row_number)
);

CREATE TABLE IF NOT EXISTS loan_products (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    criteria JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_url TEXT NOT NULL DEFAULT '',
    source_mode TEXT NOT NULL DEFAULT 'unknown'
        CHECK (source_mode IN ('public', 'fixture', 'unknown')),
    retrieved_at TIMESTAMPTZ,
    content_sha256 TEXT NOT NULL DEFAULT '',
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS matches (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    upload_batch_id UUID NOT NULL REFERENCES upload_batches(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    loan_product_id UUID NOT NULL REFERENCES loan_products(id) ON DELETE RESTRICT,
    decision TEXT NOT NULL CHECK (decision IN ('eligible', 'may_qualify', 'ineligible', 'unknown')),
    evaluated_criteria JSONB NOT NULL DEFAULT '{}'::jsonb,
    unknown_criteria JSONB NOT NULL DEFAULT '{}'::jsonb,
    reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT matches_unique_pair UNIQUE (upload_batch_id, user_id, loan_product_id)
);

CREATE TABLE IF NOT EXISTS workflow_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    upload_batch_id UUID NOT NULL REFERENCES upload_batches(id) ON DELETE CASCADE,
    n8n_execution_id TEXT,
    workflow_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'started'
        CHECK (status IN ('started', 'completed', 'failed')),
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    error_message TEXT,
    CONSTRAINT workflow_runs_execution_unique UNIQUE (n8n_execution_id)
);

CREATE TABLE IF NOT EXISTS email_deliveries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    upload_batch_id UUID NOT NULL REFERENCES upload_batches(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    recipient TEXT NOT NULL,
    template_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'sent', 'failed')),
    provider_message_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at TIMESTAMPTZ,
    error_message TEXT,
    CONSTRAINT email_deliveries_unique_attempt
        UNIQUE (upload_batch_id, user_id, recipient, template_key)
);

CREATE TABLE IF NOT EXISTS ingestion_errors (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    upload_batch_id UUID NOT NULL REFERENCES upload_batches(id) ON DELETE CASCADE,
    source_row_number INTEGER NOT NULL CHECK (source_row_number >= 2),
    error_message TEXT NOT NULL,
    raw_data JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ingestion_errors_upload_row_unique
        UNIQUE (upload_batch_id, source_row_number)
);

CREATE TABLE IF NOT EXISTS processed_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS users_batch_idx ON users (upload_batch_id);
CREATE INDEX IF NOT EXISTS users_user_id_idx ON users (user_id);
CREATE INDEX IF NOT EXISTS matches_batch_decision_idx
    ON matches (upload_batch_id, decision);
CREATE INDEX IF NOT EXISTS workflow_runs_batch_idx ON workflow_runs (upload_batch_id);
CREATE INDEX IF NOT EXISTS ingestion_errors_batch_idx
    ON ingestion_errors (upload_batch_id, source_row_number);

-- Keep retries safe when this file is applied to a database created by the
-- earlier local schema while adding provenance needed by Workflow A.
ALTER TABLE loan_products ADD COLUMN IF NOT EXISTS source_url TEXT NOT NULL DEFAULT '';
ALTER TABLE loan_products ADD COLUMN IF NOT EXISTS source_mode TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE loan_products ADD COLUMN IF NOT EXISTS retrieved_at TIMESTAMPTZ;
ALTER TABLE loan_products ADD COLUMN IF NOT EXISTS content_sha256 TEXT NOT NULL DEFAULT '';
ALTER TABLE loan_products DROP CONSTRAINT IF EXISTS loan_products_source_mode_check;
ALTER TABLE loan_products ADD CONSTRAINT loan_products_source_mode_check
    CHECK (source_mode IN ('public', 'fixture', 'unknown'));
ALTER TABLE matches DROP CONSTRAINT IF EXISTS matches_decision_check;
ALTER TABLE matches ADD CONSTRAINT matches_decision_check
    CHECK (decision IN ('eligible', 'may_qualify', 'ineligible', 'unknown'));
