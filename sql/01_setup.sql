-- SnowLens Full Edition provisioning script
-- Co-authored with CoCo
/* ---------------------------------------------------------------------------
   SnowLens — Full Edition
   Step 1 of 2: Provision database, schema, warehouse, stage, and grants.
   Run this script as ACCOUNTADMIN (or a role with equivalent privileges) in a
   Snowsight worksheet. It creates the resources SnowLens needs and grants
   read access to SNOWFLAKE.ACCOUNT_USAGE.

   All 8 detectors are computed live inside the app from ACCOUNT_USAGE — there
   is no stored procedure or results table to create here.

   Uses its own SNOWLENS_FULL objects, so it can be installed alongside the free
   Trial edition without any conflict.

   After this completes, follow the README to upload the Python files to the
   created stage, then run 02_create_app.sql to create the Streamlit app.
---------------------------------------------------------------------------- */
USE ROLE ACCOUNTADMIN;
/* 1. Dedicated warehouse. Small, auto-suspend after 60s.
      Total idle cost is essentially zero. */
CREATE WAREHOUSE IF NOT EXISTS SNOWLENS_FULL_WH
    WITH WAREHOUSE_SIZE = 'XSMALL'
         AUTO_SUSPEND    = 60
         AUTO_RESUME     = TRUE
         INITIALLY_SUSPENDED = TRUE
         COMMENT = 'Warehouse used by the SnowLens Full Edition app';
/* 2. Dedicated database + schema. Kept separate so uninstall is a one-liner. */
CREATE DATABASE IF NOT EXISTS SNOWLENS_FULL
    COMMENT = 'SnowLens Full Edition (from vizcanvaz.com)';
CREATE SCHEMA IF NOT EXISTS SNOWLENS_FULL.APP;
USE DATABASE SNOWLENS_FULL;
USE SCHEMA   APP;
/* 3. Stage that will hold the Streamlit source files.
      SNOWFLAKE_SSE encryption keeps files at rest encrypted with a
      Snowflake-managed key. */
CREATE STAGE IF NOT EXISTS SNOWLENS_STAGE
    ENCRYPTION = (TYPE = 'SNOWFLAKE_SSE')
    COMMENT = 'Streamlit source files for SnowLens Full Edition';
/* 4. Role that will own the app and be granted read on ACCOUNT_USAGE.
      Kept separate from ACCOUNTADMIN so day-to-day use is least-privilege. */
CREATE ROLE IF NOT EXISTS SNOWLENS_FULL_ROLE
    COMMENT = 'Role used to run the SnowLens Full Edition app';
/* Read access to Snowflake's metadata views. This is what powers all 8
   detectors. It does NOT grant access to any of your data. */
GRANT IMPORTED PRIVILEGES ON DATABASE SNOWFLAKE          TO ROLE SNOWLENS_FULL_ROLE;
/* App-object privileges. */
GRANT USAGE ON WAREHOUSE SNOWLENS_FULL_WH                TO ROLE SNOWLENS_FULL_ROLE;
GRANT USAGE ON DATABASE  SNOWLENS_FULL                   TO ROLE SNOWLENS_FULL_ROLE;
GRANT USAGE ON SCHEMA    SNOWLENS_FULL.APP               TO ROLE SNOWLENS_FULL_ROLE;
GRANT READ, WRITE ON STAGE SNOWLENS_FULL.APP.SNOWLENS_STAGE TO ROLE SNOWLENS_FULL_ROLE;
GRANT CREATE STREAMLIT   ON SCHEMA SNOWLENS_FULL.APP     TO ROLE SNOWLENS_FULL_ROLE;
/* 5. Grant the new role to the current user so you can use it right away.
      Replace the username below if you want a different user to run the app. */
SET current_user_name = CURRENT_USER();
GRANT ROLE SNOWLENS_FULL_ROLE TO USER IDENTIFIER($current_user_name);
/* ------------------------------------------------------------------------- */
SELECT 'SnowLens Full Edition setup complete. Next: upload files to the stage and run 02_create_app.sql.'
    AS status;
