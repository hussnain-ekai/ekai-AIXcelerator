DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM schema_migrations WHERE version = '010') THEN
        RAISE NOTICE 'Migration 010 already applied, skipping';
        RETURN;
    END IF;

    CREATE TABLE IF NOT EXISTS profile_checkpoints (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        data_product_id UUID NOT NULL REFERENCES data_products(id) ON DELETE CASCADE,
        database_name   TEXT NOT NULL,
        schema_name     TEXT NOT NULL,
        current_table   TEXT,
        processed_tables TEXT[] NOT NULL DEFAULT '{}',
        requested_tables TEXT[] NOT NULL DEFAULT '{}',
        results         JSONB NOT NULL DEFAULT '{}',
        status          TEXT NOT NULL DEFAULT 'in_progress'
                        CHECK (status IN ('in_progress', 'completed', 'paused')),
        created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
    );

    CREATE UNIQUE INDEX IF NOT EXISTS idx_profile_checkpoints_dp
        ON profile_checkpoints(data_product_id);

    CREATE OR REPLACE FUNCTION update_profile_checkpoint_timestamp()
    RETURNS TRIGGER AS $t$
    BEGIN NEW.updated_at = now(); RETURN NEW; END;
    $t$ LANGUAGE plpgsql;

    DROP TRIGGER IF EXISTS trg_profile_checkpoint_updated ON profile_checkpoints;
    CREATE TRIGGER trg_profile_checkpoint_updated
        BEFORE UPDATE ON profile_checkpoints
        FOR EACH ROW EXECUTE FUNCTION update_profile_checkpoint_timestamp();

    INSERT INTO schema_migrations (version, filename, checksum_sha256)
    VALUES ('010', 'migrate-010-profile-checkpoints.sql', encode(sha256('010-profile-checkpoints'::bytea), 'hex'));

END $$;
