-- ============================================================================
-- Artifact Versioning Migration
-- Adds version column with auto-increment trigger to artifacts table
-- ============================================================================
--
-- Run this BEFORE consolidate-artifacts.sql
-- Run with: psql $DATABASE_URL -f scripts/migrate-artifact-versioning.sql
--
-- ============================================================================

-- Add quality_report to artifact_type enum if not exists
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_enum
        WHERE enumlabel = 'quality_report'
        AND enumtypid = (SELECT oid FROM pg_type WHERE typname = 'artifact_type')
    ) THEN
        ALTER TYPE artifact_type ADD VALUE 'quality_report';
    END IF;
END $$;

-- Add version column (default 1 for existing rows)
ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1;

-- Add unique constraint to prevent duplicate versions
-- This ensures each (data_product_id, artifact_type, version) combination is unique
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'uq_artifact_version'
    ) THEN
        ALTER TABLE artifacts
        ADD CONSTRAINT uq_artifact_version UNIQUE (data_product_id, artifact_type, version);
    END IF;
EXCEPTION WHEN unique_violation THEN
    -- Constraint already exists or data violates it - will be fixed by consolidation script
    RAISE NOTICE 'Unique constraint uq_artifact_version could not be added - run consolidate-artifacts.sql first';
END $$;

-- Auto-increment trigger for artifact versioning
CREATE OR REPLACE FUNCTION trigger_auto_version_artifact()
RETURNS TRIGGER AS $$
BEGIN
    -- Always compute the next version based on existing artifacts
    SELECT COALESCE(MAX(version), 0) + 1 INTO NEW.version
    FROM artifacts
    WHERE data_product_id = NEW.data_product_id
      AND artifact_type = NEW.artifact_type;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Drop existing trigger if it exists (for idempotency)
DROP TRIGGER IF EXISTS auto_version_artifact ON artifacts;

-- Create trigger that fires before insert
CREATE TRIGGER auto_version_artifact
    BEFORE INSERT ON artifacts
    FOR EACH ROW EXECUTE FUNCTION trigger_auto_version_artifact();

-- Index for efficient latest-version queries
-- Allows fast: SELECT * FROM artifacts WHERE data_product_id = $1 AND artifact_type = $2 ORDER BY version DESC LIMIT 1
CREATE INDEX IF NOT EXISTS idx_art_product_type_version
ON artifacts (data_product_id, artifact_type, version DESC);

-- ============================================================================
-- Verification
-- ============================================================================
-- After running, verify with:
--   SELECT column_name, data_type, column_default
--   FROM information_schema.columns
--   WHERE table_name = 'artifacts' AND column_name = 'version';
--
--   SELECT conname FROM pg_constraint WHERE conrelid = 'artifacts'::regclass;
--
--   SELECT tgname FROM pg_trigger WHERE tgrelid = 'artifacts'::regclass;
-- ============================================================================
