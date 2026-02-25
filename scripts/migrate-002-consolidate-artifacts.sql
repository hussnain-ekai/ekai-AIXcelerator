-- ============================================================================
-- Artifact Consolidation Migration
-- Removes duplicate artifacts, keeping only the latest per type per data product
-- ============================================================================
--
-- Run this AFTER migrate-artifact-versioning.sql (if unique constraint failed)
-- Run with: psql $DATABASE_URL -f scripts/consolidate-artifacts.sql
--
-- ============================================================================

-- Step 1: Show current state (for verification)
DO $$
DECLARE
    total_count INTEGER;
    unique_combinations INTEGER;
BEGIN
    SELECT COUNT(*) INTO total_count FROM artifacts;
    SELECT COUNT(DISTINCT (data_product_id, artifact_type)) INTO unique_combinations FROM artifacts;
    RAISE NOTICE 'Before consolidation: % total artifacts, % unique (product, type) combinations', total_count, unique_combinations;
END $$;

-- Step 2: Delete all but the latest artifact for each (data_product_id, artifact_type) combination
-- Keep the one with the most recent created_at timestamp
WITH latest AS (
    SELECT DISTINCT ON (data_product_id, artifact_type) id
    FROM artifacts
    ORDER BY data_product_id, artifact_type, created_at DESC
)
DELETE FROM artifacts
WHERE id NOT IN (SELECT id FROM latest);

-- Step 3: Reset all versions to 1 (since we now have only one artifact per type)
UPDATE artifacts SET version = 1;

-- Step 4: Show state after consolidation
DO $$
DECLARE
    total_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO total_count FROM artifacts;
    RAISE NOTICE 'After consolidation: % total artifacts (should equal unique combinations)', total_count;
END $$;

-- Step 5: Now add the unique constraint if it doesn't exist
-- (This should succeed now that duplicates are removed)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'uq_artifact_version'
    ) THEN
        ALTER TABLE artifacts
        ADD CONSTRAINT uq_artifact_version UNIQUE (data_product_id, artifact_type, version);
        RAISE NOTICE 'Unique constraint uq_artifact_version added successfully';
    ELSE
        RAISE NOTICE 'Unique constraint uq_artifact_version already exists';
    END IF;
END $$;

-- ============================================================================
-- Verification
-- ============================================================================
-- After running, verify with:
--   SELECT data_product_id, artifact_type, COUNT(*) as cnt
--   FROM artifacts
--   GROUP BY data_product_id, artifact_type
--   HAVING COUNT(*) > 1;
--
-- Should return 0 rows (no duplicates)
-- ============================================================================
