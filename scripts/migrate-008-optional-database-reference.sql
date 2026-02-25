-- Migration 008: Make database_reference optional for document-only products
-- Adds product_type column and relaxes database_reference NOT NULL constraint

BEGIN;

-- Add product_type column with default 'structured' for backward compatibility
ALTER TABLE data_products
  ADD COLUMN IF NOT EXISTS product_type TEXT NOT NULL DEFAULT 'structured';

-- Add CHECK constraint to validate product_type values
ALTER TABLE data_products
  ADD CONSTRAINT chk_product_type
  CHECK (product_type IN ('structured', 'document', 'hybrid'));

-- Make database_reference nullable so document-only products can omit it
ALTER TABLE data_products
  ALTER COLUMN database_reference DROP NOT NULL;

-- Ensure structured products still require database_reference
-- (enforced at application layer via Zod, but add a safety constraint)
ALTER TABLE data_products
  ADD CONSTRAINT chk_structured_requires_db
  CHECK (product_type <> 'structured' OR database_reference IS NOT NULL);

COMMIT;
