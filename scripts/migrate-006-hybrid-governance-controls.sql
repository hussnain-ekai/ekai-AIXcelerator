-- ============================================================================
-- Hybrid Intelligence Governance Controls Migration (HYB-DATA-005)
-- ============================================================================
-- Purpose:
--   Adds retention, legal-hold, and governance-audit controls for document
--   semantic assets and answer evidence traces.
--
-- Run with:
--   psql "$DATABASE_URL" -f scripts/migrate-hybrid-governance-controls.sql
-- ============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- 1) Governance columns on canonical registry
-- ---------------------------------------------------------------------------
ALTER TABLE doc_registry
  ADD COLUMN IF NOT EXISTS retention_until TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS legal_hold BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS governance_tags JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS idx_doc_registry_retention
  ON doc_registry (data_product_id, retention_until)
  WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_doc_registry_legal_hold
  ON doc_registry (data_product_id, legal_hold)
  WHERE deleted_at IS NULL;

-- ---------------------------------------------------------------------------
-- 2) Legal hold ledger
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS doc_legal_holds (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  data_product_id  UUID NOT NULL REFERENCES data_products(id) ON DELETE CASCADE,
  document_id      UUID NOT NULL REFERENCES uploaded_documents(id) ON DELETE CASCADE,
  hold_status      VARCHAR(16) NOT NULL DEFAULT 'active'
                   CHECK (hold_status IN ('active', 'released')),
  hold_reason      TEXT NOT NULL,
  hold_ref         VARCHAR(128),
  created_by       VARCHAR(256) NOT NULL,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  released_by      VARCHAR(256),
  released_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_doc_legal_holds_active
  ON doc_legal_holds (data_product_id, document_id, hold_status, created_at DESC);

-- ---------------------------------------------------------------------------
-- 3) Governance audit log + retention jobs
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS doc_governance_audit (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  data_product_id  UUID NOT NULL REFERENCES data_products(id) ON DELETE CASCADE,
  document_id      UUID REFERENCES uploaded_documents(id) ON DELETE SET NULL,
  event_type       VARCHAR(64) NOT NULL,
  actor            VARCHAR(256) NOT NULL,
  details          JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_doc_governance_audit_product_time
  ON doc_governance_audit (data_product_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_doc_governance_audit_event
  ON doc_governance_audit (data_product_id, event_type, created_at DESC);

CREATE TABLE IF NOT EXISTS doc_retention_jobs (
  id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  data_product_id      UUID REFERENCES data_products(id) ON DELETE SET NULL,
  started_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at          TIMESTAMPTZ,
  status               VARCHAR(24) NOT NULL DEFAULT 'running'
                       CHECK (status IN ('running', 'completed', 'failed')),
  expired_candidates   INTEGER NOT NULL DEFAULT 0,
  deleted_documents    INTEGER NOT NULL DEFAULT 0,
  skipped_legal_hold   INTEGER NOT NULL DEFAULT 0,
  actor                VARCHAR(256) NOT NULL DEFAULT 'system_retention',
  details              JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_doc_retention_jobs_product_time
  ON doc_retention_jobs (data_product_id, started_at DESC);

-- ---------------------------------------------------------------------------
-- 4) Helper triggers: keep doc_registry.legal_hold in sync with hold ledger
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION sync_doc_registry_legal_hold() RETURNS TRIGGER AS $$
DECLARE
  target_doc UUID;
  target_dp UUID;
  has_active BOOLEAN;
BEGIN
  target_doc := COALESCE(NEW.document_id, OLD.document_id);
  target_dp := COALESCE(NEW.data_product_id, OLD.data_product_id);

  SELECT EXISTS (
    SELECT 1
    FROM doc_legal_holds h
    WHERE h.data_product_id = target_dp
      AND h.document_id = target_doc
      AND h.hold_status = 'active'
  ) INTO has_active;

  UPDATE doc_registry
     SET legal_hold = has_active,
         updated_at = now()
   WHERE data_product_id = target_dp
     AND document_id = target_doc;

  RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_trigger WHERE tgname = 'tr_doc_legal_hold_sync'
  ) THEN
    CREATE TRIGGER tr_doc_legal_hold_sync
      AFTER INSERT OR UPDATE OR DELETE ON doc_legal_holds
      FOR EACH ROW
      EXECUTE FUNCTION sync_doc_registry_legal_hold();
  END IF;
END $$;

-- ---------------------------------------------------------------------------
-- 5) Retention sweep function with auditable output
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION apply_document_retention(
  p_data_product_id UUID DEFAULT NULL,
  p_now TIMESTAMPTZ DEFAULT now(),
  p_actor VARCHAR DEFAULT 'system_retention'
) RETURNS JSONB AS $$
DECLARE
  v_job_id UUID := gen_random_uuid();
  v_candidates INTEGER := 0;
  v_deleted INTEGER := 0;
  v_skipped_hold INTEGER := 0;
BEGIN
  INSERT INTO doc_retention_jobs (
    id, data_product_id, started_at, status, actor, details
  ) VALUES (
    v_job_id, p_data_product_id, now(), 'running', p_actor,
    jsonb_build_object('effective_now', p_now)
  );

  SELECT COUNT(*)
    INTO v_candidates
  FROM doc_registry r
  WHERE (p_data_product_id IS NULL OR r.data_product_id = p_data_product_id)
    AND r.deleted_at IS NULL
    AND r.retention_until IS NOT NULL
    AND r.retention_until <= p_now;

  SELECT COUNT(*)
    INTO v_skipped_hold
  FROM doc_registry r
  WHERE (p_data_product_id IS NULL OR r.data_product_id = p_data_product_id)
    AND r.deleted_at IS NULL
    AND r.retention_until IS NOT NULL
    AND r.retention_until <= p_now
    AND COALESCE(r.legal_hold, FALSE) = TRUE;

  WITH to_delete AS (
    SELECT r.data_product_id, r.document_id
    FROM doc_registry r
    WHERE (p_data_product_id IS NULL OR r.data_product_id = p_data_product_id)
      AND r.deleted_at IS NULL
      AND r.retention_until IS NOT NULL
      AND r.retention_until <= p_now
      AND COALESCE(r.legal_hold, FALSE) = FALSE
  ),
  registry_mark AS (
    UPDATE doc_registry r
       SET deleted_at = now(),
           metadata = COALESCE(r.metadata, '{}'::jsonb)
             || jsonb_build_object(
                  'retention_deleted_at', now(),
                  'retention_actor', p_actor
                )
      FROM to_delete d
     WHERE r.data_product_id = d.data_product_id
       AND r.document_id = d.document_id
    RETURNING r.data_product_id, r.document_id, r.retention_until
  ),
  upload_mark AS (
    UPDATE uploaded_documents u
       SET is_deleted = TRUE,
           deleted_at = now(),
           deleted_by = p_actor
      FROM registry_mark d
     WHERE u.id = d.document_id
       AND COALESCE(u.is_deleted, FALSE) = FALSE
    RETURNING u.id
  ),
  audit_mark AS (
    INSERT INTO doc_governance_audit (
      id, data_product_id, document_id, event_type, actor, details
    )
    SELECT
      gen_random_uuid(),
      d.data_product_id,
      d.document_id,
      'retention_delete',
      p_actor,
      jsonb_build_object(
        'retention_until', d.retention_until,
        'job_id', v_job_id
      )
    FROM registry_mark d
    RETURNING id
  )
  SELECT COUNT(*) INTO v_deleted FROM registry_mark;

  UPDATE doc_retention_jobs
     SET finished_at = now(),
         status = 'completed',
         expired_candidates = v_candidates,
         deleted_documents = v_deleted,
         skipped_legal_hold = v_skipped_hold,
         details = details
           || jsonb_build_object(
                'result', 'ok',
                'deleted_documents', v_deleted,
                'skipped_legal_hold', v_skipped_hold
              )
   WHERE id = v_job_id;

  RETURN jsonb_build_object(
    'job_id', v_job_id,
    'status', 'completed',
    'expired_candidates', v_candidates,
    'deleted_documents', v_deleted,
    'skipped_legal_hold', v_skipped_hold
  );
EXCEPTION WHEN OTHERS THEN
  UPDATE doc_retention_jobs
     SET finished_at = now(),
         status = 'failed',
         details = details || jsonb_build_object('error', SQLERRM)
   WHERE id = v_job_id;
  RAISE;
END;
$$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------------------
-- 6) RLS for new governance tables
-- ---------------------------------------------------------------------------
ALTER TABLE doc_legal_holds ENABLE ROW LEVEL SECURITY;
ALTER TABLE doc_governance_audit ENABLE ROW LEVEL SECURITY;
ALTER TABLE doc_retention_jobs ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public'
      AND tablename = 'doc_legal_holds'
      AND policyname = 'doc_legal_holds_via_product'
  ) THEN
    CREATE POLICY doc_legal_holds_via_product ON doc_legal_holds
      USING (data_product_id IN (SELECT id FROM data_products));
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public'
      AND tablename = 'doc_governance_audit'
      AND policyname = 'doc_governance_audit_via_product'
  ) THEN
    CREATE POLICY doc_governance_audit_via_product ON doc_governance_audit
      USING (data_product_id IN (SELECT id FROM data_products));
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public'
      AND tablename = 'doc_retention_jobs'
      AND policyname = 'doc_retention_jobs_via_product'
  ) THEN
    CREATE POLICY doc_retention_jobs_via_product ON doc_retention_jobs
      USING (data_product_id IS NULL OR data_product_id IN (SELECT id FROM data_products));
  END IF;
END $$;

COMMIT;
