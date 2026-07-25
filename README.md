# SnowLens — Full Edition

Snowflake cost & performance observability that runs entirely inside your own
Snowflake account. No data ever leaves your environment. No outbound network
calls. All 8 detectors, configurable detection windows, and cost attribution
by workload.

It runs on Streamlit-in-Snowflake and reads only Snowflake's own
`ACCOUNT_USAGE` views (`QUERY_HISTORY`, `WAREHOUSE_METERING_HISTORY`,
`QUERY_ATTRIBUTION_HISTORY`). Nothing is sent externally.

Installs into its own `SNOWLENS_FULL` objects, so it can run alongside the free
Trial edition with no conflict.

---

## What gets detected — all 9 rules

**Performance**
- **long_running** — queries that took longer than 10 seconds
- **cancelled** — queries manually stopped or cancelled by a timeout
- **failed** — queries that errored out (non-cancellation)
- **spilling** — queries that ran out of memory and spilled to local/remote disk
- **queued queries** - queries in queue due to more workloads,enabling multi-cluster warehouse is the best approach

**Cost**
- **credit_spike** — a warehouse used far more credits in an hour than its own recent average (>2σ)
- **idle_running** — a warehouse burned credits with zero queries running
- **oversized_warehouse** — a warehouse sized larger than its workload needs
- **workload_cost_spike** — a tagged workload (or role, if untagged) is a cost outlier vs your other workloads

Plus **Cost Attribution by Workload** (dbt-optional universal tagging), which
groups credit usage by `QUERY_TAG`, detected dbt model, or role.

Every flagged event comes with a plain-English recommendation and a specific
next step — not just a number.

---

## Configurable detection windows (Full Edition)

The Trial is fixed at a 6-hour (performance) / 7-day (cost) window. The Full
Edition lets you widen either window right in the app — no SQL, no code change:

- Open the app → **⚙️ Detection window settings**
- Set the performance window (hours) and cost window (7 / 30 / 90 days)
- Results update immediately

Detection is computed live from `ACCOUNT_USAGE` each time the app loads (and
whenever you change the window), so results are always current.

---

## Installation

**Prereqs:** ACCOUNTADMIN role, a Snowflake account (any edition — Standard
works fine). Same simple 3-step flow as the Trial.

### 1. Run the setup script

Open a new Snowsight worksheet, paste in `sql/01_setup.sql`, and run it. It
creates:

- Database `SNOWLENS_FULL` + schema `SNOWLENS_FULL.APP`
- Warehouse `SNOWLENS_FULL_WH` (XSMALL, auto-suspend 60s)
- Stage `SNOWLENS_FULL.APP.SNOWLENS_STAGE`
- Role `SNOWLENS_FULL_ROLE` with least-privilege grants (incl. `IMPORTED
  PRIVILEGES` on the `SNOWFLAKE` database so it can read `ACCOUNT_USAGE`)

### 2. Upload the Python files to the stage

In Snowsight:

1. Go to **Data → Databases → SNOWLENS_FULL → APP → Stages → SNOWLENS_STAGE**
2. Click **+ Files**
3. Upload both files from the `src/` folder:
   - `streamlit_app.py`
   - `environment.yml`

### 3. Create the Streamlit app

Open a new Snowsight worksheet, paste in `sql/02_create_app.sql`, and run it.
Then open **Projects → Streamlit → SNOWLENS_APP** in Snowsight.

---

## Keeping results fresh

There's nothing to schedule or re-run — detection is computed live each time the
app loads, and again whenever you change the detection window. Results are cached
for 5 minutes to keep the app responsive.

Note: `ACCOUNT_USAGE` views lag from a few minutes up to a few hours — results
reflect the most recent data Snowflake has made available.

---

## Improving attribution accuracy

The "cost by workload" view attributes credit usage using the most specific
signal available: an explicit `QUERY_TAG`, then a detected dbt model, then the
executing role as a fallback. Setting `QUERY_TAG` in whatever runs your queries
(Airflow, Matillion, cron, etc.) sharpens it regardless of your stack:

```sql
ALTER SESSION SET QUERY_TAG = 'nightly_orders_job';
```

---

## Uninstall

Run `sql/99_uninstall.sql` as ACCOUNTADMIN. It drops the `SNOWLENS_FULL`
database, `SNOWLENS_FULL_WH` warehouse, and `SNOWLENS_FULL_ROLE` role.

---

## Cost to run

XSMALL warehouse, auto-suspends after 60s — a few cents per session while the
app is open, essentially zero when idle. Storage is negligible.

---

## Privacy & security

SnowLens reads only `SNOWFLAKE.ACCOUNT_USAGE` metadata — no customer data, no
query results — and makes no outbound network calls. VizCanvaz never receives
any data from your account.

## License

See `LICENSE.md`. Free , Life-time license for use in any single Snowflake account.

## Support

Priority email support: **vizcanvas@gmail.com** (include your Razorpay payment
reference for the fastest response).

© 2026 VizCanvaz · vizcanvaz.com
