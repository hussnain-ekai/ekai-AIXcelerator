-- ekaiX SPCS Application Setup Script
-- Creates compute pools, services, and stored procedures

-- ============================================================
-- 1. Application schema
-- ============================================================
CREATE SCHEMA IF NOT EXISTS app;
CREATE SCHEMA IF NOT EXISTS app_internal;

-- ============================================================
-- 2. Image repository
-- ============================================================
CREATE IMAGE REPOSITORY IF NOT EXISTS app.image_repo;

-- ============================================================
-- 3. Compute pools
-- ============================================================
CREATE COMPUTE POOL IF NOT EXISTS ekaix_frontend_pool
  MIN_NODES = 1
  MAX_NODES = 1
  INSTANCE_FAMILY = CPU_X64_XS
  AUTO_RESUME = TRUE
  AUTO_SUSPEND_SECS = 300;

CREATE COMPUTE POOL IF NOT EXISTS ekaix_backend_pool
  MIN_NODES = 1
  MAX_NODES = 2
  INSTANCE_FAMILY = CPU_X64_XS
  AUTO_RESUME = TRUE
  AUTO_SUSPEND_SECS = 300;

CREATE COMPUTE POOL IF NOT EXISTS ekaix_ai_pool
  MIN_NODES = 1
  MAX_NODES = 2
  INSTANCE_FAMILY = CPU_X64_S
  AUTO_RESUME = TRUE
  AUTO_SUSPEND_SECS = 600;

-- ============================================================
-- 4. Services
-- ============================================================
CREATE SERVICE IF NOT EXISTS app.frontend_service
  IN COMPUTE POOL ekaix_frontend_pool
  FROM SPECIFICATION_FILE = 'spcs_services/frontend/spec.yaml'
  EXTERNAL_ACCESS_INTEGRATIONS = ()
  MIN_INSTANCES = 1
  MAX_INSTANCES = 1;

CREATE SERVICE IF NOT EXISTS app.backend_service
  IN COMPUTE POOL ekaix_backend_pool
  FROM SPECIFICATION_FILE = 'spcs_services/backend/spec.yaml'
  EXTERNAL_ACCESS_INTEGRATIONS = ()
  MIN_INSTANCES = 1
  MAX_INSTANCES = 2;

CREATE SERVICE IF NOT EXISTS app.ai_service
  IN COMPUTE POOL ekaix_ai_pool
  FROM SPECIFICATION_FILE = 'spcs_services/ai-service/spec.yaml'
  EXTERNAL_ACCESS_INTEGRATIONS = ()
  MIN_INSTANCES = 1
  MAX_INSTANCES = 2;

-- ============================================================
-- 5. Service endpoints
-- ============================================================
GRANT USAGE ON SERVICE app.frontend_service TO APPLICATION ROLE app_user;

-- ============================================================
-- 6. Stored procedures (EXECUTE AS CALLER for RCR)
-- ============================================================
CREATE OR REPLACE PROCEDURE app.discover_schema(database_name VARCHAR, schema_name VARCHAR)
  RETURNS VARIANT
  LANGUAGE PYTHON
  RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  HANDLER = 'stored_procedures.discover_schema.run'
  EXECUTE AS CALLER;

CREATE OR REPLACE PROCEDURE app.profile_table(fqn VARCHAR)
  RETURNS VARIANT
  LANGUAGE PYTHON
  RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python')
  HANDLER = 'stored_procedures.profile_table.run'
  EXECUTE AS CALLER;

CREATE OR REPLACE PROCEDURE app.validate_semantic_view(yaml_content VARCHAR)
  RETURNS VARIANT
  LANGUAGE PYTHON
  RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python', 'pyyaml')
  HANDLER = 'stored_procedures.validate_semantic_view.run'
  EXECUTE AS CALLER;

CREATE OR REPLACE PROCEDURE app.publish_cortex_agent(config_json VARCHAR)
  RETURNS VARIANT
  LANGUAGE PYTHON
  RUNTIME_VERSION = '3.11'
  PACKAGES = ('snowflake-snowpark-python', 'pyyaml')
  HANDLER = 'stored_procedures.publish_cortex_agent.run'
  EXECUTE AS CALLER;

-- ============================================================
-- 7. Application roles and grants
-- ============================================================
CREATE APPLICATION ROLE IF NOT EXISTS app_user;
CREATE APPLICATION ROLE IF NOT EXISTS app_admin;

GRANT USAGE ON SCHEMA app TO APPLICATION ROLE app_user;
GRANT USAGE ON SCHEMA app TO APPLICATION ROLE app_admin;
GRANT USAGE ON PROCEDURE app.discover_schema(VARCHAR, VARCHAR) TO APPLICATION ROLE app_user;
GRANT USAGE ON PROCEDURE app.profile_table(VARCHAR) TO APPLICATION ROLE app_user;
GRANT USAGE ON PROCEDURE app.validate_semantic_view(VARCHAR) TO APPLICATION ROLE app_user;
GRANT USAGE ON PROCEDURE app.publish_cortex_agent(VARCHAR) TO APPLICATION ROLE app_admin;

-- ============================================================
-- 8. Reference callback for consumer-granted privileges
-- ============================================================
CREATE OR REPLACE PROCEDURE app_internal.config_reference_callback(
  ref_name VARCHAR, operation VARCHAR, ref_or_alias VARCHAR
)
  RETURNS VARCHAR
  LANGUAGE SQL
  AS
  $$
    BEGIN
      CASE (operation)
        WHEN 'ADD' THEN SELECT SYSTEM$SET_REFERENCE(:ref_name, :ref_or_alias);
        WHEN 'REMOVE' THEN SELECT SYSTEM$REMOVE_REFERENCE(:ref_name);
        WHEN 'CLEAR' THEN SELECT SYSTEM$REMOVE_REFERENCE(:ref_name);
      END CASE;
      RETURN 'Done';
    END;
  $$;

GRANT USAGE ON PROCEDURE app_internal.config_reference_callback(VARCHAR, VARCHAR, VARCHAR)
  TO APPLICATION ROLE app_admin;
