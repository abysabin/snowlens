/* ---------------------------------------------------------------------------
   SnowLens — Full Edition
   Step 2 of 2: Create the Streamlit app.

   Prerequisites (from the README):
     1. 01_setup.sql has been run successfully.
     2. streamlit_app.py and environment.yml have been uploaded to the stage
        @SNOWLENS_FULL.APP.SNOWLENS_STAGE via Snowsight (Data > Databases >
        SNOWLENS_FULL > APP > Stages > SNOWLENS_STAGE > + Files).
---------------------------------------------------------------------------- */

USE ROLE      SNOWLENS_FULL_ROLE;
USE DATABASE  SNOWLENS_FULL;
USE SCHEMA    APP;
USE WAREHOUSE SNOWLENS_FULL_WH;

/* Sanity check: confirm both required files are present. */
LIST @SNOWLENS_STAGE;

CREATE OR REPLACE STREAMLIT SNOWLENS_APP
    ROOT_LOCATION = '@SNOWLENS_FULL.APP.SNOWLENS_STAGE'
    MAIN_FILE     = 'streamlit_app.py'
    QUERY_WAREHOUSE = SNOWLENS_FULL_WH
    COMMENT = 'SnowLens Full Edition — vizcanvaz.com';

/* Grant usage on the app so the runner role can open it. */
GRANT USAGE ON STREAMLIT SNOWLENS_FULL.APP.SNOWLENS_APP TO ROLE SNOWLENS_FULL_ROLE;

SELECT 'SnowLens Full Edition app created. Open it from Snowsight > Streamlit.' AS status;
