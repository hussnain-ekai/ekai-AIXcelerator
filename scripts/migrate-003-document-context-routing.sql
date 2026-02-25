-- ============================================================================
-- Document Context Routing Migration
-- ============================================================================
-- Purpose:
--   Adds context versioning + evidence routing primitives so document uploads
--   can be activated per mission step (discovery/requirements/modeling/etc.)
--   with user-driven control and auditability.
--
-- Run with:
--   psql "$DATABASE_URL" -f scripts/migrate-document-context-routing.sql
-- ============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- 1) Extend uploaded_documents with lifecycle and classification metadata
-- ---------------------------------------------------------------------------
ALTER TABLE uploaded_documents
  ADD COLUMN IF NOT EXISTS source_channel VARCHAR(32) NOT NULL DEFAULT 'documents_panel',
  ADD COLUMN IF NOT EXISTS user_note TEXT,
  ADD COLUMN IF NOT EXISTS doc_kind VARCHAR(64),
  ADD COLUMN IF NOT EXISTS summary TEXT,
  ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS deleted_by VARCHAR(256);

-- ---------------------------------------------------------------------------
-- 2) Context versions (immutable snapshots)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS context_versions (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  data_product_id UUID NOT NULL REFERENCES data_products(id) ON DELETE CASCADE,
  version        INTEGER NOT NULL,
  reason         VARCHAR(64) NOT NULL DEFAULT 'system',
  changed_by     VARCHAR(256) NOT NULL,
  change_summary JSONB NOT NULL DEFAULT '{}',
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_context_versions UNIQUE (data_product_id, version)
);

CREATE OR REPLACE FUNCTION trigger_auto_version_context() RETURNS TRIGGER AS $$
BEGIN
  IF NEW.version IS NULL OR NEW.version <= 1 THEN
    SELECT COALESCE(MAX(version), 0) + 1
    INTO NEW.version
    FROM context_versions
    WHERE data_product_id = NEW.data_product_id;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_trigger
    WHERE tgname = 'auto_version_context'
  ) THEN
    CREATE TRIGGER auto_version_context
      BEFORE INSERT ON context_versions
      FOR EACH ROW
      EXECUTE FUNCTION trigger_auto_version_context();
  END IF;
END $$;

-- Link uploaded documents to the context version where they were last changed.
ALTER TABLE uploaded_documents
  ADD COLUMN IF NOT EXISTS context_version_id UUID REFERENCES context_versions(id) ON DELETE SET NULL;

-- ---------------------------------------------------------------------------
-- 3) Evidence registry (structured facts extracted from documents)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS document_evidence (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  data_product_id UUID NOT NULL REFERENCES data_products(id) ON DELETE CASCADE,
  document_id     UUID NOT NULL REFERENCES uploaded_documents(id) ON DELETE CASCADE,
  evidence_type   VARCHAR(64) NOT NULL,
  step_candidates TEXT[] NOT NULL DEFAULT '{}',
  impact_scope    TEXT[] NOT NULL DEFAULT '{}',
  payload         JSONB NOT NULL DEFAULT '{}',
  provenance      JSONB NOT NULL DEFAULT '{}',
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- 4) Per-step context selection state (candidate/active/reference/excluded)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS context_step_selections (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  data_product_id   UUID NOT NULL REFERENCES data_products(id) ON DELETE CASCADE,
  step_name         VARCHAR(32) NOT NULL,
  document_id       UUID NOT NULL REFERENCES uploaded_documents(id) ON DELETE CASCADE,
  evidence_id       UUID NOT NULL REFERENCES document_evidence(id) ON DELETE CASCADE,
  state             VARCHAR(32) NOT NULL DEFAULT 'candidate',
  selected_by       VARCHAR(256) NOT NULL,
  context_version_id UUID REFERENCES context_versions(id) ON DELETE SET NULL,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT chk_context_step_state
    CHECK (state IN ('candidate', 'active', 'reference', 'excluded')),
  CONSTRAINT uq_context_step_evidence
    UNIQUE (data_product_id, step_name, evidence_id)
);

CREATE OR REPLACE FUNCTION trigger_set_context_updated_at() RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_trigger
    WHERE tgname = 'set_context_updated_at'
  ) THEN
    CREATE TRIGGER set_context_updated_at
      BEFORE UPDATE ON context_step_selections
      FOR EACH ROW
      EXECUTE FUNCTION trigger_set_context_updated_at();
  END IF;
END $$;

-- ---------------------------------------------------------------------------
-- 5) Artifact context snapshots (traceability for BRD/YAML/publish outputs)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS artifact_context_snapshots (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  data_product_id  UUID NOT NULL REFERENCES data_products(id) ON DELETE CASCADE,
  artifact_id      UUID REFERENCES artifacts(id) ON DELETE SET NULL,
  artifact_type    VARCHAR(64) NOT NULL,
  artifact_version INTEGER,
  context_version_id UUID REFERENCES context_versions(id) ON DELETE SET NULL,
  snapshot         JSONB NOT NULL DEFAULT '{}',
  created_by       VARCHAR(256) NOT NULL,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- 6) Indexes
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_ud_deleted ON uploaded_documents (data_product_id, is_deleted, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ctx_versions_product ON context_versions (data_product_id, version DESC);
CREATE INDEX IF NOT EXISTS idx_doc_evidence_product ON document_evidence (data_product_id, document_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_doc_evidence_step_candidates ON document_evidence USING GIN (step_candidates);
CREATE INDEX IF NOT EXISTS idx_ctx_step_product_step ON context_step_selections (data_product_id, step_name, state, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_ctx_step_document ON context_step_selections (document_id);
CREATE INDEX IF NOT EXISTS idx_art_ctx_product ON artifact_context_snapshots (data_product_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_art_ctx_context_version ON artifact_context_snapshots (context_version_id);

-- ---------------------------------------------------------------------------
-- 7) RLS + policies
-- ---------------------------------------------------------------------------
ALTER TABLE context_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE document_evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE context_step_selections ENABLE ROW LEVEL SECURITY;
ALTER TABLE artifact_context_snapshots ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public'
      AND tablename = 'context_versions'
      AND policyname = 'ctx_versions_via_product'
  ) THEN
    CREATE POLICY ctx_versions_via_product ON context_versions
      USING (data_product_id IN (SELECT id FROM data_products));
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public'
      AND tablename = 'document_evidence'
      AND policyname = 'doc_evidence_via_product'
  ) THEN
    CREATE POLICY doc_evidence_via_product ON document_evidence
      USING (data_product_id IN (SELECT id FROM data_products));
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public'
      AND tablename = 'context_step_selections'
      AND policyname = 'context_step_via_product'
  ) THEN
    CREATE POLICY context_step_via_product ON context_step_selections
      USING (data_product_id IN (SELECT id FROM data_products));
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public'
      AND tablename = 'artifact_context_snapshots'
      AND policyname = 'artifact_context_via_product'
  ) THEN
    CREATE POLICY artifact_context_via_product ON artifact_context_snapshots
      USING (data_product_id IN (SELECT id FROM data_products));
  END IF;
END $$;

COMMIT;
