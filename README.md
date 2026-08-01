# SnowLens — Free Edition

Snowflake cost & performance observability that runs entirely inside your own
Snowflake account. No data ever leaves your environment. No outbound network
calls. Completely free.

It runs on Streamlit-in-Snowflake and reads only Snowflake's own
`ACCOUNT_USAGE` views (`QUERY_HISTORY`, `WAREHOUSE_METERING_HISTORY`,
`QUERY_ATTRIBUTION_HISTORY`). Nothing is sent externally.

---

## Two apps

SnowLens installs as two Streamlit apps that share one stage and one warehouse:

| App | What it does |
| --- | --- |
| **`SNOWLENS_APP`** | The anomaly dashboard — 9 detectors, configurable detection windows, and cost attribution by workload. |
| **`SNOWLENS_SIZING_ADVISOR`** | The warehouse sizing advisor — analyses spill, queuing, scan volume and concurrency per warehouse, then recommends a size and cluster count with generated `ALTER WAREHOUSE` statements. |

---

## What gets detected — all 9 rules

**Performance**
- **long_running** — queries exceeding a configurable threshold (10s / 20s / 30s / 60s), with optional SELECT-only filter
- **cancelled** — queries manually stopped or cancelled by a timeout
- **failed** — queries that errored out (non-cancellation)
- **spilling** — queries that ran out of memory and spilled to local/remote disk (remote flagged as high risk)
- **queuing** — warehouses with cumulative queued overload time >60s, indicating concurrency contention

**Cost**
- **credit_spike** — a warehouse used far more credits in an hour than its own recent average (>2σ)
- **idle_running** — a warehouse burned credits with zero queries running
- **oversized_warehouse** — a warehouse sized larger than its workload needs
- **workload_cost_spike** — a tagged workload (or role, if untagged) is a cost outlier vs your other workloads

Plus **Cost Attribution by Workload**, which groups credit usage by
`QUERY_TAG`, detected dbt model, or role.

Every flagged event comes with a plain-English recommendation and a specific
next step — not just a number.

---

## Warehouse Sizing Advisor

`SNOWLENS_SIZING_ADVISOR` answers a different question from the detectors:
not "what went wrong?" but "is each warehouse the right size?"

It derives a recommendation per warehouse from observed behaviour over a
7 / 30 / 90-day window:

| Signal | What it means |
| --- | --- |
| **Remote spill rate** | Share of queries spilling to remote storage. The strongest undersizing signal — remote spill is dramatically slower than memory or local SSD. |
| **Local spill rate** | Share spilling to local SSD. A softer signal, but sustained local spill still costs time. |
| **p90 bytes scanned** | 90th-percentile scan per query, so a few large queries don't mask an otherwise small workload. |
| **Cumulative queue time** | `QUEUED_OVERLOAD_TIME` per warehouse — a **concurrency** signal, fixed by scaling out, not up. |
| **Peak concurrency** | Busiest hour's query count, used to size the cluster range. |
| **Idle credit hours** | Hours that burned credits with zero queries — points at `AUTO_SUSPEND`. |

**Decision logic**

1. **Size up** if remote spill > 2% of queries, or local spill > 15%
2. **Size down** if p90 scan < 100 MB, the warehouse is above Small, and spill is under 1%
3. **Add clusters** (independently of size) if cumulative queue time > 60s
4. **Otherwise keep** the current size

Each recommendation carries a confidence level based on how many queries backed
it, a plain-English explanation of why, and an estimated credits/hour delta. The
app generates ready-to-review `ALTER WAREHOUSE` statements — it never executes
them.

---

## Memory safety for large accounts

SnowLens includes hardening for Streamlit-in-Snowflake's ~32 MB memory limit:

- Query text truncated to 300 characters
- Each detector capped to top 200 findings by severity
- Cost attribution aggregated in SQL before loading into the app
- Warehouse scope selector to limit detection to individual warehouses

These measures keep the app responsive even on accounts with thousands of daily
queries and 90-day detection windows.

---

## Installation

**Prereqs:** ACCOUNTADMIN role, a Snowflake account (any edition — Standard
works fine).

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
3. Upload all three files from the `src/` folder:
   - `streamlit_app.py`
   - `sizing_advisor.py`
   - `environment.yml`

### 3. Create the Streamlit apps

Open a new Snowsight worksheet, paste in `sql/02_create_app.sql`, and run it.
It creates both apps. Then open **Projects → Streamlit** in Snowsight, where
you'll find:

- **SNOWLENS_APP** — the anomaly dashboard
- **SNOWLENS_SIZING_ADVISOR** — the warehouse sizing advisor

---

## Keeping results fresh

Both apps compute live from ACCOUNT_USAGE every time they run or their settings
change. There is no stored procedure and no results table — they query
ACCOUNT_USAGE directly in the Streamlit session.

Note: `ACCOUNT_USAGE` views lag from a few minutes up to a few hours — results
reflect the most recent data available from Snowflake.

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
database (including both apps), `SNOWLENS_FULL_WH` warehouse, and
`SNOWLENS_FULL_ROLE` role.

---

## Cost to run

XSMALL warehouse, auto-suspends after 60s — a few cents per session while the
app is open, essentially zero when idle. Storage is negligible.

---

## Privacy & security

Both apps read only `SNOWFLAKE.ACCOUNT_USAGE` metadata — no customer data, no
query results — and make no outbound network calls. VizCanvaz never receives
any data from your account. The sizing advisor generates `ALTER WAREHOUSE`
statements for you to review, but never executes them.

## License

Free to use. No payment, no signup, no expiry.

## Support

Email: **vizcanvas@gmail.com**

© 2026 VizCanvaz · vizcanvaz.com
