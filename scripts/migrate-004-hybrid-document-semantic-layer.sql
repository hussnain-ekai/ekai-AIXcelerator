-- ============================================================================
-- Hybrid Intelligence Document Semantic Layer Migration
-- ============================================================================
-- Purpose:
--   Adds canonical document-semantic tables used by hybrid Q/A:
--   - doc_registry
--   - doc_chunks
--   - doc_entities
--   - doc_facts
--   - doc_fact_links
--   - qa_evidence
--
-- Run with:
--   psql "$DATABASE_URL" -f scripts/migrate-hybrid-document-semantic-layer.sql
-- ============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- 1) Canonical document registry
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS doc_registry (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  data_product_id         UUID NOT NULL REFERENCES data_products(id) ON DELETE CASCADE,
  document_id             UUID NOT NULL REFERENCES uploaded_documents(id) ON DELETE CASCADE,
  source_system           VARCHAR(64) NOT NULL DEFAULT 'ekaix_upload',
  source_uri              TEXT,
  title                   VARCHAR(512) NOT NULL,
  mime_type               VARCHAR(128),
  checksum_sha256         CHAR(64),
  version_id              INTEGER NOT NULL DEFAULT 1,
  uploaded_by             VARCHAR(256) NOT NULL,
  uploaded_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at              TIMESTAMPTZ,
  extraction_status       extraction_status NOT NULL DEFAULT 'pending',
  extraction_method       VARCHAR(64),
  parse_quality_score     NUMERIC(5,2),
  extraction_diagnostics  JSONB NOT NULL DEFAULT '{}'::jsonb,
  metadata                JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_doc_registry_doc UNIQUE (data_product_id, document_id)
);

CREATE OR REPLACE FUNCTION trigger_set_doc_registry_updated_at() RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_trigger WHERE tgname = 'set_doc_registry_updated_at'
  ) THEN
    CREATE TRIGGER set_doc_registry_updated_at
      BEFORE UPDATE ON doc_registry
      FOR EACH ROW
      EXECUTE FUNCTION trigger_set_doc_registry_updated_at();
  END IF;
END $$;

-- ---------------------------------------------------------------------------
-- 2) Chunk store
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS doc_chunks (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  data_product_id         UUID NOT NULL REFERENCES data_products(id) ON DELETE CASCADE,
  document_id             UUID NOT NULL REFERENCES uploaded_documents(id) ON DELETE CASCADE,
  registry_id             UUID NOT NULL REFERENCES doc_registry(id) ON DELETE CASCADE,
  chunk_seq               INTEGER NOT NULL,
  section_path            TEXT,
  page_no                 INTEGER,
  chunk_text              TEXT NOT NULL,
  embedding_ref           TEXT,
  acl_scope               JSONB NOT NULL DEFAULT '{}'::jsonb,
  parser_version          VARCHAR(64),
  extraction_confidence   NUMERIC(5,4),
  created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_doc_chunk_seq UNIQUE (document_id, chunk_seq)
);

-- ---------------------------------------------------------------------------
-- 3) Entity store
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS doc_entities (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  data_product_id  UUID NOT NULL REFERENCES data_products(id) ON DELETE CASCADE,
  document_id      UUID NOT NULL REFERENCES uploaded_documents(id) ON DELETE CASCADE,
  chunk_id         UUID REFERENCES doc_chunks(id) ON DELETE SET NULL,
  entity_type      VARCHAR(64) NOT NULL,
  canonical_value  TEXT,
  raw_value        TEXT,
  start_offset     INTEGER,
  end_offset       INTEGER,
  confidence       NUMERIC(5,4),
  metadata         JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- 4) Fact store
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS doc_facts (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  data_product_id  UUID NOT NULL REFERENCES data_products(id) ON DELETE CASCADE,
  document_id      UUID NOT NULL REFERENCES uploaded_documents(id) ON DELETE CASCADE,
  chunk_id         UUID REFERENCES doc_chunks(id) ON DELETE SET NULL,
  fact_type        VARCHAR(64) NOT NULL,
  subject_key      TEXT,
  predicate        TEXT,
  object_value     TEXT,
  object_unit      TEXT,
  numeric_value    NUMERIC,
  event_time       TIMESTAMPTZ,
  currency         VARCHAR(16),
  confidence       NUMERIC(5,4),
  source_page      INTEGER,
  metadata         JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- 5) Fact-to-enterprise links
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS doc_fact_links (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  data_product_id  UUID NOT NULL REFERENCES data_products(id) ON DELETE CASCADE,
  fact_id          UUID NOT NULL REFERENCES doc_facts(id) ON DELETE CASCADE,
  target_domain    VARCHAR(64) NOT NULL,
  target_key       VARCHAR(256) NOT NULL,
  link_reason      TEXT,
  link_confidence  NUMERIC(5,4),
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_doc_fact_link UNIQUE (fact_id, target_domain, target_key)
);

-- ---------------------------------------------------------------------------
-- 6) Answer evidence log
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS qa_evidence (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  data_product_id  UUID NOT NULL REFERENCES data_products(id) ON DELETE CASCADE,
  query_id         VARCHAR(128) NOT NULL,
  answer_id        VARCHAR(128),
  source_mode      VARCHAR(32) NOT NULL DEFAULT 'unknown',
  confidence       VARCHAR(16) NOT NULL DEFAULT 'medium',
  exactness_state  VARCHAR(48) NOT NULL DEFAULT 'not_applicable',
  tool_calls       JSONB NOT NULL DEFAULT '[]'::jsonb,
  sql_refs         JSONB NOT NULL DEFAULT '[]'::jsonb,
  fact_refs        JSONB NOT NULL DEFAULT '[]'::jsonb,
  chunk_refs       JSONB NOT NULL DEFAULT '[]'::jsonb,
  conflicts        JSONB NOT NULL DEFAULT '[]'::jsonb,
  recovery_plan    JSONB NOT NULL DEFAULT '{}'::jsonb,
  final_decision   VARCHAR(32) NOT NULL DEFAULT 'answer_ready',
  created_by       VARCHAR(256) NOT NULL,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- 7) Indexes
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_doc_registry_product ON doc_registry (data_product_id, uploaded_at DESC);
CREATE INDEX IF NOT EXISTS idx_doc_registry_document ON doc_registry (document_id);
CREATE INDEX IF NOT EXISTS idx_doc_registry_status ON doc_registry (data_product_id, extraction_status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_doc_registry_deleted ON doc_registry (data_product_id, deleted_at);

CREATE INDEX IF NOT EXISTS idx_doc_chunks_product ON doc_chunks (data_product_id, document_id, chunk_seq);
CREATE INDEX IF NOT EXISTS idx_doc_chunks_registry ON doc_chunks (registry_id, chunk_seq);
CREATE INDEX IF NOT EXISTS idx_doc_chunks_text_gin ON doc_chunks USING GIN (to_tsvector('english', chunk_text));

CREATE INDEX IF NOT EXISTS idx_doc_entities_product_type ON doc_entities (data_product_id, entity_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_doc_entities_canonical ON doc_entities (data_product_id, canonical_value);

CREATE INDEX IF NOT EXISTS idx_doc_facts_product_type ON doc_facts (data_product_id, fact_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_doc_facts_subject ON doc_facts (data_product_id, subject_key);
CREATE INDEX IF NOT EXISTS idx_doc_facts_event_time ON doc_facts (data_product_id, event_time DESC);
CREATE INDEX IF NOT EXISTS idx_doc_facts_numeric ON doc_facts (data_product_id, numeric_value);

CREATE INDEX IF NOT EXISTS idx_doc_fact_links_product ON doc_fact_links (data_product_id, target_domain, target_key);
CREATE INDEX IF NOT EXISTS idx_doc_fact_links_fact ON doc_fact_links (fact_id);

CREATE INDEX IF NOT EXISTS idx_qa_evidence_product_query ON qa_evidence (data_product_id, query_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_qa_evidence_decision ON qa_evidence (data_product_id, final_decision, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_qa_evidence_source_mode ON qa_evidence (data_product_id, source_mode, created_at DESC);

-- ---------------------------------------------------------------------------
-- 8) RLS + policies
-- ---------------------------------------------------------------------------
ALTER TABLE doc_registry ENABLE ROW LEVEL SECURITY;
ALTER TABLE doc_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE doc_entities ENABLE ROW LEVEL SECURITY;
ALTER TABLE doc_facts ENABLE ROW LEVEL SECURITY;
ALTER TABLE doc_fact_links ENABLE ROW LEVEL SECURITY;
ALTER TABLE qa_evidence ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public'
      AND tablename = 'doc_registry'
      AND policyname = 'doc_registry_via_product'
  ) THEN
    CREATE POLICY doc_registry_via_product ON doc_registry
      USING (data_product_id IN (SELECT id FROM data_products));
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public'
      AND tablename = 'doc_chunks'
      AND policyname = 'doc_chunks_via_product'
  ) THEN
    CREATE POLICY doc_chunks_via_product ON doc_chunks
      USING (data_product_id IN (SELECT id FROM data_products));
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public'
      AND tablename = 'doc_entities'
      AND policyname = 'doc_entities_via_product'
  ) THEN
    CREATE POLICY doc_entities_via_product ON doc_entities
      USING (data_product_id IN (SELECT id FROM data_products));
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public'
      AND tablename = 'doc_facts'
      AND policyname = 'doc_facts_via_product'
  ) THEN
    CREATE POLICY doc_facts_via_product ON doc_facts
      USING (data_product_id IN (SELECT id FROM data_products));
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public'
      AND tablename = 'doc_fact_links'
      AND policyname = 'doc_fact_links_via_product'
  ) THEN
    CREATE POLICY doc_fact_links_via_product ON doc_fact_links
      USING (data_product_id IN (SELECT id FROM data_products));
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public'
      AND tablename = 'qa_evidence'
      AND policyname = 'qa_evidence_via_product'
  ) THEN
    CREATE POLICY qa_evidence_via_product ON qa_evidence
      USING (data_product_id IN (SELECT id FROM data_products));
  END IF;
END $$;

COMMIT;
