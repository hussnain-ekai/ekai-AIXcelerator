-- ============================================================================
-- Hybrid Intelligence Search Views Migration
-- ============================================================================
-- Purpose:
--   Adds curated retrieval surfaces for document search and fact lookup:
--   - v_doc_search_chunks
--   - v_doc_search_facts
--
-- Run with:
--   psql "$DATABASE_URL" -f scripts/migrate-hybrid-doc-search-views.sql
-- ============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- 1) Chunk retrieval surface
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_doc_search_chunks AS
SELECT
  c.id AS chunk_id,
  c.data_product_id,
  c.document_id,
  c.registry_id,
  dr.version_id,
  dr.title AS document_title,
  ud.filename,
  ud.content_type AS mime_type,
  c.chunk_seq,
  c.section_path,
  c.page_no,
  c.chunk_text,
  setweight(to_tsvector('english', COALESCE(c.chunk_text, '')), 'A') AS search_vector,
  COALESCE(c.acl_scope, '{}'::jsonb) AS acl_scope,
  c.extraction_confidence,
  c.created_at
FROM doc_chunks c
JOIN doc_registry dr
  ON dr.id = c.registry_id
 AND dr.deleted_at IS NULL
JOIN uploaded_documents ud
  ON ud.id = c.document_id
 AND COALESCE(ud.is_deleted, false) = false;

COMMENT ON VIEW v_doc_search_chunks IS
  'Curated retrieval view for document chunk search with ACL metadata.';

-- ---------------------------------------------------------------------------
-- 2) Fact retrieval surface
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_doc_search_facts AS
SELECT
  f.id AS fact_id,
  f.data_product_id,
  f.document_id,
  f.chunk_id,
  dr.version_id,
  dr.title AS document_title,
  ud.filename,
  ud.content_type AS mime_type,
  f.fact_type,
  f.subject_key,
  f.predicate,
  f.object_value,
  f.object_unit,
  f.numeric_value,
  f.currency,
  f.event_time,
  f.source_page,
  f.confidence,
  f.metadata,
  to_tsvector(
    'english',
    CONCAT_WS(' ',
      COALESCE(f.subject_key, ''),
      COALESCE(f.predicate, ''),
      COALESCE(f.object_value, '')
    )
  ) AS search_vector,
  COALESCE(linked.links, '[]'::jsonb) AS enterprise_links,
  COALESCE(dr.metadata -> 'acl_scope', '{}'::jsonb) AS acl_scope,
  f.created_at
FROM doc_facts f
JOIN doc_registry dr
  ON dr.document_id = f.document_id
 AND dr.data_product_id = f.data_product_id
 AND dr.deleted_at IS NULL
JOIN uploaded_documents ud
  ON ud.id = f.document_id
 AND COALESCE(ud.is_deleted, false) = false
LEFT JOIN LATERAL (
  SELECT jsonb_agg(
           jsonb_build_object(
             'target_domain', l.target_domain,
             'target_key', l.target_key,
             'link_reason', l.link_reason,
             'link_confidence', l.link_confidence
           )
         ) AS links
  FROM doc_fact_links l
  WHERE l.fact_id = f.id
) linked ON TRUE;

COMMENT ON VIEW v_doc_search_facts IS
  'Curated retrieval view for normalized document facts and semantic-model links.';

COMMIT;
