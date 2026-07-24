/* ---------------------------------------------------------------------------
   SnowLens — Full Edition
   Uninstall script. Removes every object 01_setup.sql created.

   Run as ACCOUNTADMIN.
---------------------------------------------------------------------------- */

USE ROLE ACCOUNTADMIN;

DROP DATABASE  IF EXISTS SNOWLENS_FULL;
DROP WAREHOUSE IF EXISTS SNOWLENS_FULL_WH;
DROP ROLE      IF EXISTS SNOWLENS_FULL_ROLE;

SELECT 'SnowLens Full Edition uninstalled. All objects removed.' AS status;
