-- ============================================================================
-- Agent Artifact Tables Migration (009)
-- ============================================================================
-- Purpose:
--   Creates tables that AI service agent tools write to during the pipeline.
--   These were referenced in postgres_tools.py and modeling_tools.py but never
--   had a migration — causing "relation does not exist" errors at runtime.
--
-- Tables:
--   data_descriptions   — structured data description docs (discovery phase)
--   data_catalog        — Gold layer table/column documentation (modeling phase)
--   business_glossary   — business term → physical column mapping (modeling phase)
--   metrics_definitions — KPI formulas linked to fact columns (modeling phase)
--   validation_rules    — grain checks, referential integrity (modeling phase)
--
-- Run with:
--   docker exec <postgres-container> psql -U ekaix -d ekaix \
--     -f /scripts/migrate-009-agent-artifact-tables.sql
-- ============================================================================

BEGIN;

-- Guard: skip if already applied
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM schema_migrations WHERE version = '009'
  ) THEN
    RAISE NOTICE 'Migration 009 already applied, skipping';
    RETURN;
  END IF;
END $$;

-- ---------------------------------------------------------------------------
-- 1) data_descriptions — persisted by save_data_description tool
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS data_descriptions (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  data_product_id  UUID NOT NULL REFERENCES data_products(id) ON DELETE CASCADE,
  description_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  version          INTEGER NOT NULL DEFAULT 1,
  created_by       VARCHAR(256) NOT NULL DEFAULT 'system',
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_data_descriptions_dp
  ON data_descriptions (data_product_id, version DESC);

-- ---------------------------------------------------------------------------
-- 2) data_catalog — persisted by save_data_catalog tool
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS data_catalog (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  data_product_id  UUID NOT NULL REFERENCES data_products(id) ON DELETE CASCADE,
  catalog_json     JSONB NOT NULL DEFAULT '{}'::jsonb,
  version          INTEGER NOT NULL DEFAULT 1,
  created_by       VARCHAR(256) NOT NULL DEFAULT 'system',
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_data_catalog_dp
  ON data_catalog (data_product_id, version DESC);

-- ---------------------------------------------------------------------------
-- 3) business_glossary — persisted by save_business_glossary tool
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS business_glossary (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  data_product_id  UUID NOT NULL REFERENCES data_products(id) ON DELETE CASCADE,
  glossary_json    JSONB NOT NULL DEFAULT '{}'::jsonb,
  version          INTEGER NOT NULL DEFAULT 1,
  created_by       VARCHAR(256) NOT NULL DEFAULT 'system',
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_business_glossary_dp
  ON business_glossary (data_product_id, version DESC);

-- ---------------------------------------------------------------------------
-- 4) metrics_definitions — persisted by save_metrics_definitions tool
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS metrics_definitions (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  data_product_id  UUID NOT NULL REFERENCES data_products(id) ON DELETE CASCADE,
  metrics_json     JSONB NOT NULL DEFAULT '{}'::jsonb,
  version          INTEGER NOT NULL DEFAULT 1,
  created_by       VARCHAR(256) NOT NULL DEFAULT 'system',
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_metrics_definitions_dp
  ON metrics_definitions (data_product_id, version DESC);

-- ---------------------------------------------------------------------------
-- 5) validation_rules — persisted by save_validation_rules tool
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS validation_rules (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  data_product_id  UUID NOT NULL REFERENCES data_products(id) ON DELETE CASCADE,
  rules_json       JSONB NOT NULL DEFAULT '{}'::jsonb,
  version          INTEGER NOT NULL DEFAULT 1,
  created_by       VARCHAR(256) NOT NULL DEFAULT 'system',
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_validation_rules_dp
  ON validation_rules (data_product_id, version DESC);

-- ---------------------------------------------------------------------------
-- 6) Record migration
-- ---------------------------------------------------------------------------
INSERT INTO schema_migrations (version, filename, checksum_sha256)
VALUES (
  '009',
  'migrate-009-agent-artifact-tables.sql',
  encode(sha256('migrate-009-agent-artifact-tables.sql'::bytea), 'hex')
)
ON CONFLICT (version) DO NOTHING;

COMMIT;
