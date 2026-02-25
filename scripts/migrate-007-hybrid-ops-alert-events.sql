-- Hybrid Ops Alert Events migration
-- Purpose:
--   Persist operational alert events so dashboards and chaos checks can prove
--   that alerts are firing (HYB-OPS-001 / HYB-OPS-002 acceptance evidence).
--
-- Usage:
--   psql "$DATABASE_URL" -f scripts/migrate-hybrid-ops-alert-events.sql

BEGIN;

CREATE TABLE IF NOT EXISTS ops_alert_events (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  data_product_id UUID NOT NULL REFERENCES data_products(id) ON DELETE CASCADE,
  signal          VARCHAR(64) NOT NULL,
  severity        VARCHAR(16) NOT NULL DEFAULT 'warning',
  message         TEXT NOT NULL,
  source_service  VARCHAR(32) NOT NULL,
  source_route    VARCHAR(128) NOT NULL,
  session_id      VARCHAR(128),
  query_id        VARCHAR(128),
  metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_by      VARCHAR(256) NOT NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ops_alert_events_product_created
  ON ops_alert_events (data_product_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_ops_alert_events_signal_created
  ON ops_alert_events (data_product_id, signal, created_at DESC);

ALTER TABLE ops_alert_events ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_policies
    WHERE schemaname = 'public'
      AND tablename = 'ops_alert_events'
      AND policyname = 'ops_alert_events_via_product'
  ) THEN
    CREATE POLICY ops_alert_events_via_product ON ops_alert_events
      USING (data_product_id IN (SELECT id FROM data_products));
  END IF;
END $$;

COMMIT;
