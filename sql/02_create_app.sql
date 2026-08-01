/* ---------------------------------------------------------------------------
   SnowLens
   Step 2 of 2: Create the Streamlit apps.

   Creates two apps that share one stage:
     SNOWLENS_APP            — the 9-detector anomaly dashboard
     SNOWLENS_SIZING_ADVISOR — data-driven warehouse sizing recommendations

   Prerequisites (from the README):
     1. 01_setup.sql has been run successfully.
     2. streamlit_app.py, sizing_advisor.py and environment.yml have been
        uploaded to the stage @SNOWLENS_FULL.APP.SNOWLENS_STAGE via Snowsight
        (Data > Databases > SNOWLENS_FULL > APP > Stages > SNOWLENS_STAGE >
        + Files).
---------------------------------------------------------------------------- */

USE ROLE      SNOWLENS_FULL_ROLE;
USE DATABASE  SNOWLENS_FULL;
USE SCHEMA    APP;
USE WAREHOUSE SNOWLENS_FULL_WH;

/* Sanity check: confirm all three required files are present. */
LIST @SNOWLENS_STAGE;

/* --- App 1: anomaly detection dashboard --- */
CREATE OR REPLACE STREAMLIT SNOWLENS_APP
    ROOT_LOCATION = '@SNOWLENS_FULL.APP.SNOWLENS_STAGE'
    MAIN_FILE     = 'streamlit_app.py'
    QUERY_WAREHOUSE = SNOWLENS_FULL_WH
    COMMENT = 'SnowLens — cost & performance anomaly detection — vizcanvaz.com';

/* --- App 2: warehouse sizing advisor --- */
CREATE OR REPLACE STREAMLIT SNOWLENS_SIZING_ADVISOR
    ROOT_LOCATION = '@SNOWLENS_FULL.APP.SNOWLENS_STAGE'
    MAIN_FILE     = 'sizing_advisor.py'
    QUERY_WAREHOUSE = SNOWLENS_FULL_WH
    COMMENT = 'SnowLens — warehouse sizing advisor — vizcanvaz.com';

/* Grant usage on both apps so the runner role can open them. */
GRANT USAGE ON STREAMLIT SNOWLENS_FULL.APP.SNOWLENS_APP            TO ROLE SNOWLENS_FULL_ROLE;
GRANT USAGE ON STREAMLIT SNOWLENS_FULL.APP.SNOWLENS_SIZING_ADVISOR TO ROLE SNOWLENS_FULL_ROLE;

SELECT 'SnowLens apps created: SNOWLENS_APP and SNOWLENS_SIZING_ADVISOR. Open them from Snowsight > Streamlit.' AS status;
