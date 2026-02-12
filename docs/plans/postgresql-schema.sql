-- ============================================================================
-- ekaiX AIXcelerator — Complete PostgreSQL Schema
-- PostgreSQL 18.1 | February 2026
-- ============================================================================
--
-- Features used:
--   - uuidv7() for timestamp-ordered primary keys (PG18 native)
--   - Virtual generated columns for JSONB field extraction (PG18)
--   - Row-Level Security (RLS) for workspace isolation
--   - JSONB with GIN indexes for flexible state storage
--   - Partitioning on audit_logs by timestamp
--
-- Run order: Extensions → Types → Tables → Indexes → RLS → Triggers
-- ============================================================================

-- ============================================================================
-- 1. EXTENSIONS
-- ============================================================================

-- pgcrypto for gen_random_uuid() fallback (uuidv7 is PG18 native)
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ============================================================================
-- 2. CUSTOM TYPES
-- ============================================================================

CREATE TYPE data_product_status AS ENUM (
    'discovery',
    'requirements',
    'generation',
    'validation',
    'published',
    'archived'
);

CREATE TYPE share_permission AS ENUM (
    'view',
    'edit'
);

CREATE TYPE artifact_type AS ENUM (
    'erd',
    'yaml',
    'brd',
    'quality_report',
    'document',
    'export',
    'data_description',
    'data_catalog',
    'business_glossary',
    'metrics_definitions',
    'metrics',
    'validation_rules',
    'lineage'
);

CREATE TYPE extraction_status AS ENUM (
    'pending',
    'processing',
    'completed',
    'failed'
);

CREATE TYPE validation_status AS ENUM (
    'pending',
    'valid',
    'invalid',
    'warning'
);

-- ============================================================================
-- 3. TABLES
-- ============================================================================

-- ---------------------------------------------------------------------------
-- workspaces: One workspace per Snowflake user
-- ---------------------------------------------------------------------------
CREATE TABLE workspaces (
    id              UUID PRIMARY KEY DEFAULT uuidv7(),
    snowflake_user  VARCHAR(256) NOT NULL UNIQUE,
    display_name    VARCHAR(256),
    settings        JSONB NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE workspaces IS 'One workspace per Snowflake user. All data products are scoped to a workspace.';
COMMENT ON COLUMN workspaces.snowflake_user IS 'Snowflake username from Sf-Context-Current-User header.';
COMMENT ON COLUMN workspaces.settings IS 'User preferences: theme, default warehouse, notification settings.';

-- ---------------------------------------------------------------------------
-- data_products: Core entity — one per semantic model project
-- ---------------------------------------------------------------------------
CREATE TABLE data_products (
    id                  UUID PRIMARY KEY DEFAULT uuidv7(),
    workspace_id        UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    name                VARCHAR(256) NOT NULL,
    description         TEXT,
    database_reference  VARCHAR(512) NOT NULL,
    schemas             TEXT[] NOT NULL DEFAULT '{}',
    status              data_product_status NOT NULL DEFAULT 'discovery',
    state               JSONB NOT NULL DEFAULT '{}',
    health_score        SMALLINT CHECK (health_score IS NULL OR (health_score >= 0 AND health_score <= 100)),
    published_at        TIMESTAMPTZ,
    published_agent_fqn VARCHAR(512),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Virtual generated column: extract current phase from JSONB state (PG18)
    current_phase       VARCHAR(64) GENERATED ALWAYS AS (state ->> 'current_phase') VIRTUAL,

    CONSTRAINT uq_workspace_product_name UNIQUE (workspace_id, name)
);

COMMENT ON TABLE data_products IS 'A data product represents one semantic model project. Contains full Deep Agents state in JSONB.';
COMMENT ON COLUMN data_products.database_reference IS 'Snowflake database FQN that this data product references.';
COMMENT ON COLUMN data_products.schemas IS 'Array of schema names selected for discovery.';
COMMENT ON COLUMN data_products.state IS 'Full Deep Agents conversation and agent state (messages, checkpoints, todo list).';
COMMENT ON COLUMN data_products.current_phase IS 'Virtual column extracted from state JSONB for fast filtering.';

-- ---------------------------------------------------------------------------
-- data_product_shares: Sharing data products between Snowflake users
-- ---------------------------------------------------------------------------
CREATE TABLE data_product_shares (
    id               UUID PRIMARY KEY DEFAULT uuidv7(),
    data_product_id  UUID NOT NULL REFERENCES data_products(id) ON DELETE CASCADE,
    shared_with_user VARCHAR(256) NOT NULL,
    permission       share_permission NOT NULL DEFAULT 'view',
    shared_by        VARCHAR(256) NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_share_user_product UNIQUE (data_product_id, shared_with_user)
);

COMMENT ON TABLE data_product_shares IS 'Explicit sharing of data products between Snowflake users.';

-- ---------------------------------------------------------------------------
-- business_requirements: Versioned BRD documents
-- ---------------------------------------------------------------------------
CREATE TABLE business_requirements (
    id               UUID PRIMARY KEY DEFAULT uuidv7(),
    data_product_id  UUID NOT NULL REFERENCES data_products(id) ON DELETE CASCADE,
    version          INTEGER NOT NULL DEFAULT 1,
    brd_json         JSONB NOT NULL,
    is_complete      BOOLEAN NOT NULL DEFAULT false,
    created_by       VARCHAR(256) NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_brd_version UNIQUE (data_product_id, version)
);

COMMENT ON TABLE business_requirements IS 'Versioned Business Requirements Documents captured through AI conversation.';
COMMENT ON COLUMN business_requirements.brd_json IS 'Structured BRD: measures, dimensions, filters, business rules, KPIs.';

-- ---------------------------------------------------------------------------
-- semantic_views: Generated YAML semantic view definitions
-- ---------------------------------------------------------------------------
CREATE TABLE semantic_views (
    id                 UUID PRIMARY KEY DEFAULT uuidv7(),
    data_product_id    UUID NOT NULL REFERENCES data_products(id) ON DELETE CASCADE,
    version            INTEGER NOT NULL DEFAULT 1,
    yaml_content       TEXT NOT NULL,
    validation_status  validation_status NOT NULL DEFAULT 'pending',
    validation_errors  JSONB,
    validated_at       TIMESTAMPTZ,
    created_by         VARCHAR(256) NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_sv_version UNIQUE (data_product_id, version)
);

COMMENT ON TABLE semantic_views IS 'Generated Snowflake semantic view YAML definitions with validation status.';

-- ---------------------------------------------------------------------------
-- artifacts: References to files stored in MinIO (versioned)
-- ---------------------------------------------------------------------------
CREATE TABLE artifacts (
    id               UUID PRIMARY KEY DEFAULT uuidv7(),
    data_product_id  UUID NOT NULL REFERENCES data_products(id) ON DELETE CASCADE,
    artifact_type    artifact_type NOT NULL,
    version          INTEGER NOT NULL DEFAULT 1,
    minio_path       VARCHAR(1024) NOT NULL,
    filename         VARCHAR(256),
    file_size_bytes  BIGINT,
    content_type     VARCHAR(128),
    metadata         JSONB DEFAULT '{}',
    created_by       VARCHAR(256) NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_artifact_version UNIQUE (data_product_id, artifact_type, version)
);

COMMENT ON TABLE artifacts IS 'Versioned references to ERD diagrams, YAML files, BRD exports, and other artifacts stored in MinIO.';
COMMENT ON COLUMN artifacts.minio_path IS 'Full MinIO object path: {data_product_id}/{type}/v{version}/{filename}';
COMMENT ON COLUMN artifacts.version IS 'Auto-incremented version number per artifact type per data product.';

-- ---------------------------------------------------------------------------
-- uploaded_documents: User-uploaded PDFs/DOCX for context
-- ---------------------------------------------------------------------------
CREATE TABLE uploaded_documents (
    id                 UUID PRIMARY KEY DEFAULT uuidv7(),
    data_product_id    UUID NOT NULL REFERENCES data_products(id) ON DELETE CASCADE,
    filename           VARCHAR(512) NOT NULL,
    minio_path         VARCHAR(1024) NOT NULL,
    file_size_bytes    BIGINT,
    content_type       VARCHAR(128),
    extracted_content  TEXT,
    extraction_status  extraction_status NOT NULL DEFAULT 'pending',
    extraction_error   TEXT,
    uploaded_by        VARCHAR(256) NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    extracted_at       TIMESTAMPTZ
);

COMMENT ON TABLE uploaded_documents IS 'User-uploaded documents (PDF, DOCX) processed by Cortex Document AI.';

-- ---------------------------------------------------------------------------
-- data_quality_checks: Results from gold layer health checks
-- ---------------------------------------------------------------------------
CREATE TABLE data_quality_checks (
    id               UUID PRIMARY KEY DEFAULT uuidv7(),
    data_product_id  UUID NOT NULL REFERENCES data_products(id) ON DELETE CASCADE,
    overall_score    SMALLINT NOT NULL CHECK (overall_score >= 0 AND overall_score <= 100),
    check_results    JSONB NOT NULL,
    issues           JSONB NOT NULL DEFAULT '[]',
    acknowledged     BOOLEAN NOT NULL DEFAULT false,
    acknowledged_by  VARCHAR(256),
    acknowledged_at  TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE data_quality_checks IS 'Gold layer data health check results. Score >= 70 auto-passes, 40-69 requires acknowledgment, < 40 blocks.';
COMMENT ON COLUMN data_quality_checks.check_results IS 'Detailed per-check results: duplicates, type mismatches, missing descriptions, null rates.';

-- ---------------------------------------------------------------------------
-- audit_logs: Partitioned by month for scalability
-- ---------------------------------------------------------------------------
CREATE TABLE audit_logs (
    id               UUID NOT NULL DEFAULT uuidv7(),
    workspace_id     UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    data_product_id  UUID REFERENCES data_products(id) ON DELETE SET NULL,
    action_type      VARCHAR(64) NOT NULL,
    action_details   JSONB NOT NULL DEFAULT '{}',
    user_name        VARCHAR(256) NOT NULL,
    ip_address       INET,
    user_agent       TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);

COMMENT ON TABLE audit_logs IS 'Immutable audit trail partitioned by month. All user and agent actions are logged.';
COMMENT ON COLUMN audit_logs.action_type IS 'Action types: create_product, update_product, delete_product, publish, share, agent_message, discovery_start, etc.';

-- Create initial partitions (3 months ahead)
CREATE TABLE audit_logs_2026_01 PARTITION OF audit_logs
    FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');
CREATE TABLE audit_logs_2026_02 PARTITION OF audit_logs
    FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');
CREATE TABLE audit_logs_2026_03 PARTITION OF audit_logs
    FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');
CREATE TABLE audit_logs_2026_04 PARTITION OF audit_logs
    FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');
CREATE TABLE audit_logs_2026_05 PARTITION OF audit_logs
    FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE audit_logs_2026_06 PARTITION OF audit_logs
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');

-- ============================================================================
-- 4. INDEXES
-- ============================================================================

-- workspaces
CREATE INDEX idx_workspaces_snowflake_user ON workspaces (snowflake_user);

-- data_products
CREATE INDEX idx_dp_workspace_id ON data_products (workspace_id);
CREATE INDEX idx_dp_status ON data_products (status);
CREATE INDEX idx_dp_workspace_status ON data_products (workspace_id, status);
CREATE INDEX idx_dp_updated_at ON data_products (updated_at DESC);
CREATE INDEX idx_dp_state_gin ON data_products USING GIN (state jsonb_path_ops);

-- data_product_shares
CREATE INDEX idx_dps_shared_with ON data_product_shares (shared_with_user);
CREATE INDEX idx_dps_product_id ON data_product_shares (data_product_id);

-- business_requirements
CREATE INDEX idx_br_product_id ON business_requirements (data_product_id);
CREATE INDEX idx_br_product_version ON business_requirements (data_product_id, version DESC);
CREATE INDEX idx_br_brd_gin ON business_requirements USING GIN (brd_json jsonb_path_ops);

-- semantic_views
CREATE INDEX idx_sv_product_id ON semantic_views (data_product_id);
CREATE INDEX idx_sv_product_version ON semantic_views (data_product_id, version DESC);
CREATE INDEX idx_sv_validation ON semantic_views (validation_status);

-- artifacts
CREATE INDEX idx_art_product_id ON artifacts (data_product_id);
CREATE INDEX idx_art_type ON artifacts (artifact_type);
CREATE INDEX idx_art_product_type ON artifacts (data_product_id, artifact_type);
CREATE INDEX idx_art_product_type_version ON artifacts (data_product_id, artifact_type, version DESC);

-- uploaded_documents
CREATE INDEX idx_ud_product_id ON uploaded_documents (data_product_id);
CREATE INDEX idx_ud_extraction ON uploaded_documents (extraction_status);

-- data_quality_checks
CREATE INDEX idx_dqc_product_id ON data_quality_checks (data_product_id);
CREATE INDEX idx_dqc_score ON data_quality_checks (overall_score);

-- audit_logs (indexes on partitioned table)
CREATE INDEX idx_al_workspace_id ON audit_logs (workspace_id, created_at DESC);
CREATE INDEX idx_al_product_id ON audit_logs (data_product_id, created_at DESC);
CREATE INDEX idx_al_user ON audit_logs (user_name, created_at DESC);
CREATE INDEX idx_al_action ON audit_logs (action_type, created_at DESC);

-- ============================================================================
-- 5. ROW-LEVEL SECURITY (RLS)
-- ============================================================================

-- Enable RLS on all user-facing tables
ALTER TABLE workspaces ENABLE ROW LEVEL SECURITY;
ALTER TABLE data_products ENABLE ROW LEVEL SECURITY;
ALTER TABLE data_product_shares ENABLE ROW LEVEL SECURITY;
ALTER TABLE business_requirements ENABLE ROW LEVEL SECURITY;
ALTER TABLE semantic_views ENABLE ROW LEVEL SECURITY;
ALTER TABLE artifacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE uploaded_documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE data_quality_checks ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY;

-- Application sets current user via: SET app.current_user = 'SNOWFLAKE_USERNAME';
-- This is set by the backend middleware on every request using the Sf-Context-Current-User header.

-- Workspace: user can only see their own workspace
CREATE POLICY workspace_own ON workspaces
    USING (snowflake_user = current_setting('app.current_user', true));

-- Data products: user sees own products + shared products
CREATE POLICY dp_own_or_shared ON data_products
    USING (
        workspace_id IN (
            SELECT id FROM workspaces
            WHERE snowflake_user = current_setting('app.current_user', true)
        )
        OR id IN (
            SELECT data_product_id FROM data_product_shares
            WHERE shared_with_user = current_setting('app.current_user', true)
        )
    );

-- Shares: user sees shares for their products or shares made to them
CREATE POLICY shares_visibility ON data_product_shares
    USING (
        shared_with_user = current_setting('app.current_user', true)
        OR data_product_id IN (
            SELECT dp.id FROM data_products dp
            JOIN workspaces w ON dp.workspace_id = w.id
            WHERE w.snowflake_user = current_setting('app.current_user', true)
        )
    );

-- Business requirements: visible if user can see the parent data product
CREATE POLICY br_via_product ON business_requirements
    USING (
        data_product_id IN (
            SELECT id FROM data_products  -- inherits dp_own_or_shared policy
        )
    );

-- Semantic views: visible if user can see the parent data product
CREATE POLICY sv_via_product ON semantic_views
    USING (
        data_product_id IN (
            SELECT id FROM data_products
        )
    );

-- Artifacts: visible if user can see the parent data product
CREATE POLICY art_via_product ON artifacts
    USING (
        data_product_id IN (
            SELECT id FROM data_products
        )
    );

-- Uploaded documents: visible if user can see the parent data product
CREATE POLICY ud_via_product ON uploaded_documents
    USING (
        data_product_id IN (
            SELECT id FROM data_products
        )
    );

-- Data quality checks: visible if user can see the parent data product
CREATE POLICY dqc_via_product ON data_quality_checks
    USING (
        data_product_id IN (
            SELECT id FROM data_products
        )
    );

-- Audit logs: user sees logs for their workspace
CREATE POLICY al_own_workspace ON audit_logs
    USING (
        workspace_id IN (
            SELECT id FROM workspaces
            WHERE snowflake_user = current_setting('app.current_user', true)
        )
    );

-- ============================================================================
-- 6. TRIGGER FUNCTIONS
-- ============================================================================

-- Auto-update updated_at timestamp
CREATE OR REPLACE FUNCTION trigger_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Apply to tables with updated_at
CREATE TRIGGER set_updated_at_workspaces
    BEFORE UPDATE ON workspaces
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

CREATE TRIGGER set_updated_at_data_products
    BEFORE UPDATE ON data_products
    FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();

-- Auto-increment version on business_requirements insert
CREATE OR REPLACE FUNCTION trigger_auto_version_brd()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.version IS NULL OR NEW.version = 1 THEN
        SELECT COALESCE(MAX(version), 0) + 1
        INTO NEW.version
        FROM business_requirements
        WHERE data_product_id = NEW.data_product_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER auto_version_brd
    BEFORE INSERT ON business_requirements
    FOR EACH ROW EXECUTE FUNCTION trigger_auto_version_brd();

-- Auto-increment version on semantic_views insert
CREATE OR REPLACE FUNCTION trigger_auto_version_sv()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.version IS NULL OR NEW.version = 1 THEN
        SELECT COALESCE(MAX(version), 0) + 1
        INTO NEW.version
        FROM semantic_views
        WHERE data_product_id = NEW.data_product_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER auto_version_sv
    BEFORE INSERT ON semantic_views
    FOR EACH ROW EXECUTE FUNCTION trigger_auto_version_sv();

-- Auto-increment version on artifacts insert
CREATE OR REPLACE FUNCTION trigger_auto_version_artifact()
RETURNS TRIGGER AS $$
BEGIN
    SELECT COALESCE(MAX(version), 0) + 1
    INTO NEW.version
    FROM artifacts
    WHERE data_product_id = NEW.data_product_id
      AND artifact_type = NEW.artifact_type;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER auto_version_artifact
    BEFORE INSERT ON artifacts
    FOR EACH ROW EXECUTE FUNCTION trigger_auto_version_artifact();

-- ============================================================================
-- 7. APPLICATION ROLE
-- ============================================================================

-- Create application role for the backend service
-- The backend connects as this role and sets app.current_user per-request.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ekaix_app') THEN
        CREATE ROLE ekaix_app LOGIN PASSWORD 'CHANGE_ME_IN_ENV';
    END IF;
END $$;

-- Grant access to all tables
GRANT USAGE ON SCHEMA public TO ekaix_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO ekaix_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO ekaix_app;

-- Future tables get same grants
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ekaix_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO ekaix_app;

-- ============================================================================
-- 8. HELPER VIEWS
-- ============================================================================

-- View: data products with owner info and latest health score
CREATE OR REPLACE VIEW v_data_products_summary AS
SELECT
    dp.id,
    dp.name,
    dp.description,
    dp.database_reference,
    dp.status,
    dp.health_score,
    dp.current_phase,
    dp.published_at,
    dp.published_agent_fqn,
    dp.created_at,
    dp.updated_at,
    w.snowflake_user AS owner,
    (SELECT COUNT(*) FROM data_product_shares dps WHERE dps.data_product_id = dp.id) AS share_count,
    (SELECT MAX(version) FROM business_requirements br WHERE br.data_product_id = dp.id) AS latest_brd_version,
    (SELECT MAX(version) FROM semantic_views sv WHERE sv.data_product_id = dp.id) AS latest_sv_version
FROM data_products dp
JOIN workspaces w ON dp.workspace_id = w.id;

-- View: shared products visible to current user
CREATE OR REPLACE VIEW v_shared_with_me AS
SELECT
    dp.id,
    dp.name,
    dp.status,
    dp.health_score,
    dps.permission,
    dps.shared_by,
    dps.created_at AS shared_at,
    w.snowflake_user AS owner
FROM data_product_shares dps
JOIN data_products dp ON dps.data_product_id = dp.id
JOIN workspaces w ON dp.workspace_id = w.id
WHERE dps.shared_with_user = current_setting('app.current_user', true);

-- ============================================================================
-- END OF SCHEMA
-- ============================================================================
