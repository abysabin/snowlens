"""
SnowLens — Full Edition
Snowflake cost & performance observability, without leaving Snowflake.

All 9 detectors, configurable detection windows, and cost attribution by
workload. Everything is computed live in Python from
Snowflake's own ACCOUNT_USAGE views each time the app loads — there is no
stored procedure and no results table to populate.

Reads only SNOWFLAKE.ACCOUNT_USAGE (QUERY_HISTORY, WAREHOUSE_METERING_HISTORY,
QUERY_ATTRIBUTION_HISTORY). No customer data, no outbound network calls.
"""

import re
import streamlit as st
import pandas as pd
import altair as alt
from snowflake.snowpark.context import get_active_session

st.set_page_config(page_title="SnowLens — Full Edition", page_icon="❄️", layout="wide")
session = get_active_session()

CONTACT_EMAIL = "vizcanvas@gmail.com"

# Detection window defaults (Full Edition lets the user widen these in-app).
DEFAULT_PERF_HOURS = 6
DEFAULT_COST_DAYS = 7
COST_WINDOW_OPTIONS = [7, 30, 90]
SLOW_THRESHOLD_OPTIONS = [10, 20, 30, 60]  # seconds, for Slow Query Finder

RULE_INFO = {
    "long_running": {
        "label": "Slow Query Finder", "icon": "🐢", "category": "performance",
        "desc": "A query ran longer than your slow-query threshold.",
        "logic": ("Flags queries whose **total elapsed time** (queue + execution) exceeds your "
                  "**slow-query threshold** — default 10s, adjustable to 10 / 20 / 30 / 60s in "
                  "Detection settings. By default only **SELECT (read) queries** are checked "
                  "(via Snowflake's `QUERY_TYPE`), since long ETL/DML legitimately runs longer — "
                  "you can include all query types with a toggle. Checked over the performance window."),
    },
    "cancelled": {
        "label": "Cancelled Query Detector", "icon": "🚫", "category": "performance",
        "desc": "A query was manually stopped or cancelled by a timeout.",
        "logic": ("Flags queries with `EXECUTION_STATUS = 'FAIL'` whose error message mentions "
                  "'cancel' (manually stopped or timed out), over the performance window."),
    },
    "failed": {
        "label": "Query Failure Detector", "icon": "⚠️", "category": "performance",
        "desc": "A query errored out for a reason other than cancellation.",
        "logic": ("Flags queries with `EXECUTION_STATUS = 'FAIL'` that are **not** cancellations "
                  "(a genuine error), over the performance window."),
    },
    "spilling": {
        "label": "Disk Spill Detector", "icon": "💿", "category": "performance",
        "desc": "A query ran out of memory and spilled to disk. REMOTE spill is high risk.",
        "logic": ("Flags queries where `BYTES_SPILLED_TO_LOCAL_STORAGE` + "
                  "`BYTES_SPILLED_TO_REMOTE_STORAGE` > 0 — the query exceeded warehouse memory and "
                  "spilled to disk. **Remote** spill (beyond local SSD) is far slower and costlier and is "
                  "flagged as high risk. Top 200 by total bytes spilled, over the performance window."),
    },
    "queuing": {
        "label": "Queuing / Contention Detector", "icon": "⏳", "category": "performance",
        "desc": "Queries waited in a queue because the warehouse was overloaded with concurrent work.",
        "logic": ("Per warehouse, sums `QUEUED_OVERLOAD_TIME` over the performance window and flags warehouses "
                  "with heavy cumulative queuing (> 60s total) — a **concurrency** problem, not a memory one. "
                  "The fix is usually a **multi-cluster** warehouse (scales out under load), not a bigger size. "
                  "Top 200 by queue time."),
    },
    "credit_spike": {
        "label": "Cost Anomaly Detector", "icon": "💸", "category": "cost",
        "desc": "A warehouse's hourly credit usage spiked more than 2σ above its own average.",
        "logic": ("For each warehouse, computes the **mean and standard deviation** of its hourly "
                  "credit usage across the cost window, then flags any hour where "
                  "`credits > mean + 2σ`. Each warehouse is compared only to its own history."),
    },
    "idle_running": {
        "label": "Idle-But-Running Detector", "icon": "😴", "category": "cost",
        "desc": "A warehouse burned credits with zero queries running.",
        "logic": ("Flags warehouse-hours that consumed **> 0.01 credits but ran zero queries** — "
                  "usually a forgotten warehouse or an AUTO_SUSPEND set too high. Over the cost window."),
    },
    "oversized_warehouse": {
        "label": "Oversized Warehouse Detector", "icon": "📏", "category": "cost",
        "desc": "A warehouse is sized larger than its workload needs.",
        "logic": ("Flags warehouses sized **above Small** whose **average bytes scanned < 100 MB** — "
                  "the workload is tiny relative to the warehouse size, so it can likely be downsized. "
                  "Over the cost window."),
    },
    "workload_cost_spike": {
        "label": "Workload / Role Cost Spike Detector", "icon": "👤", "category": "cost",
        "desc": "A tagged workload (or role, if untagged) is a cost outlier vs other workloads.",
        "logic": ("Groups attributed credits by **workload** (`QUERY_TAG`, else detected dbt model, "
                  "else executing role) over the cost window, then flags any workload where "
                  "`credits > mean + 2σ` across all workloads."),
    },
}

# --- Custom styling ---
st.markdown("""
<style>
    [data-testid="stMetric"] {
        background: linear-gradient(135deg, #1e3a5f 0%, #2d5986 100%);
        border-radius: 12px; padding: 16px 20px; color: white;
    }
    [data-testid="stMetric"] label { color: #a8d0f0 !important; }
    [data-testid="stMetric"] [data-testid="stMetricValue"] { color: white !important; }
    .block-container { padding-top: 1.5rem; }
</style>
""", unsafe_allow_html=True)

# --- Title + Help popover ---
col_title, col_help = st.columns([6, 1])
with col_title:
    st.title("Snowflake cost & performance anomalies")
    st.caption(f"SnowLens Full Edition · all {len(RULE_INFO)} detectors · Questions? {CONTACT_EMAIL}")
with col_help:
    with st.popover("Help"):
        st.markdown("**How this works**")
        st.markdown(
            "This app runs rule-based checks live, in Python, against Snowflake's own usage "
            "views (`QUERY_HISTORY`, `WAREHOUSE_METERING_HISTORY`, `QUERY_ATTRIBUTION_HISTORY`) "
            "every time it loads. Nothing is stored — there is no procedure or results table."
        )
        st.markdown(f"**The {len(RULE_INFO)} detection rules — exact logic**")
        for subtype, info in RULE_INFO.items():
            st.markdown(f"- {info['icon']} **{info['label']}** (`{subtype}`) — {info['logic']}")
        st.markdown("**FAQ**")
        with st.expander("Why is credit_spike or workload_cost_spike sometimes empty?"):
            st.write("Both need several days of varied usage history for a meaningful baseline/comparison.")
        with st.expander("Why do I see a 'count' column?"):
            st.write(
                "The same query or warehouse condition can recur many times. Rows are grouped so you "
                "see each distinct issue once, with how many times it happened and its most recent occurrence."
            )
        with st.expander("How fresh is this data?"):
            st.write("ACCOUNT_USAGE views can lag minutes to hours. Results are cached for 5 minutes.")
        with st.expander("What if we don't use dbt?"):
            st.write(
                "Cost attribution prefers an explicit QUERY_TAG (set by any tool — Matillion, Airflow, "
                "cron, etc.), falls back to a detected dbt model name if present, and to the executing "
                "role if neither is set."
            )
        with st.expander("⚠️ Large accounts & the 90-day window (memory note)"):
            st.write(
                "Streamlit-in-Snowflake apps run with a bounded in-memory limit (about 32 MB of result "
                "data in the default warehouse runtime). To stay well under it, this app already truncates "
                "query text to a short snippet, caps each detector to its top 200 findings by severity, and "
                "aggregates cost data inside Snowflake before loading it. On a very large account with a "
                "90-day window, if a load ever feels slow or errors, use the **🏭 Warehouse scope** selector "
                "to focus on one warehouse, or shorten the cost window — both sharply cut the data pulled "
                "into the app. The heavy scanning always happens inside Snowflake; only compact results are "
                "loaded here."
            )
        st.markdown("---")
        st.markdown("**Companion app**")
        st.markdown(
            "**Warehouse Sizing Advisor** (`SNOWLENS_SIZING_ADVISOR`, under Projects > Streamlit) "
            "answers the other half of the question: not *what went wrong*, but *is each warehouse "
            "the right size*. It analyses spill, queuing, scan volume and concurrency per warehouse "
            "and generates `ALTER WAREHOUSE` statements for you to review."
        )
        st.markdown("---")
        st.caption(f"Questions or feedback? {CONTACT_EMAIL}")

# --- Warehouse scope (limits data scanned — helps large accounts stay in memory) ---
@st.cache_data(ttl=600, show_spinner=False)
def list_warehouses():
    try:
        return session.sql(
            "SELECT DISTINCT WAREHOUSE_NAME FROM SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY "
            "WHERE START_TIME >= DATEADD('day', -90, CURRENT_TIMESTAMP()) AND WAREHOUSE_NAME IS NOT NULL "
            "ORDER BY WAREHOUSE_NAME"
        ).to_pandas()["WAREHOUSE_NAME"].tolist()
    except Exception:
        return []

st.markdown("---")
warehouse_scope = st.selectbox(
    "🏭 Warehouse scope", ["All warehouses"] + list_warehouses(), index=0,
    help="Limit detection to a single warehouse to cut how much data is scanned and pulled into "
         "the app. Recommended on large accounts — especially with a 90-day window — to stay within "
         "Streamlit-in-Snowflake's in-memory limit (see Help).",
)

# --- Detection window controls (Full Edition) ---
with st.expander("⚙️ Detection window settings", expanded=False):
    st.caption(
        "Adjust the performance and cost detection windows below — "
        "results update as soon as you change these."
    )
    cw1, cw2 = st.columns(2)
    with cw1:
        perf_hours = st.number_input(
            "Performance window (hours)", min_value=1, max_value=720, value=DEFAULT_PERF_HOURS, step=1,
            help="Applies to Slow Query, Cancelled, Failure, and Disk Spill detectors.",
        )
    with cw2:
        cost_days = st.selectbox(
            "Cost window (days)", COST_WINDOW_OPTIONS, index=0,
            help="Applies to Cost Anomaly, Idle-But-Running, Oversized Warehouse, and Workload Cost Spike detectors.",
        )

    st.markdown("**Slow Query Finder**")
    sq1, sq2 = st.columns(2)
    with sq1:
        slow_threshold_s = st.selectbox(
            "Flag queries slower than", SLOW_THRESHOLD_OPTIONS, index=0,
            format_func=lambda s: f"{s} seconds",
            help="A query is 'slow' if its total elapsed time (queue + execution) exceeds this.",
        )
    with sq2:
        select_only = st.checkbox(
            "Only flag SELECT (read) queries", value=True,
            help="Recommended for BI/dashboard tuning. Long ETL/DML (INSERT, MERGE, COPY) "
                 "legitimately runs longer, so it's excluded by default. Uses Snowflake's "
                 "QUERY_TYPE, so CTEs and commented queries are still classified correctly.",
        )

perf_hours = int(perf_hours)
cost_days = int(cost_days)
slow_threshold_s = int(slow_threshold_s)


# --- Live detection: compute all 9 rules from ACCOUNT_USAGE ---
MAX_ROWS_PER_DETECTOR = 200   # cap each detector to its top-N by severity (memory guard)
QUERY_TEXT_SNIPPET = 300      # truncate stored query text to a snippet (memory guard)


@st.cache_data(ttl=300, show_spinner="Scanning your account…")
def compute_flagged_events(perf_hours: int, cost_days: int, slow_threshold_s: int,
                           select_only: bool, warehouse: str) -> pd.DataFrame:
    slow_threshold_ms = slow_threshold_s * 1000
    select_clause = "AND qh.QUERY_TYPE = 'SELECT'" if select_only else ""
    cap = MAX_ROWS_PER_DETECTOR
    snip = QUERY_TEXT_SNIPPET
    if warehouse and warehouse != "All warehouses":
        whname = warehouse.replace("'", "''")
        wh_qh = f"AND qh.WAREHOUSE_NAME = '{whname}'"     # for the aliased QUERY_HISTORY (qh)
        wh_bare = f"AND WAREHOUSE_NAME = '{whname}'"      # for the CTEs (no alias)
    else:
        wh_qh = ""
        wh_bare = ""
    sql = f"""
    WITH hourly_usage AS (
        SELECT WAREHOUSE_NAME, DATE_TRUNC('hour', START_TIME) AS usage_hour, SUM(CREDITS_USED) AS credits
        FROM SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY
        WHERE START_TIME >= DATEADD('day', -{cost_days}, CURRENT_TIMESTAMP())
          {wh_bare}
        GROUP BY WAREHOUSE_NAME, usage_hour
    ),
    wh_baseline AS (
        SELECT WAREHOUSE_NAME, AVG(credits) AS avg_credits, STDDEV(credits) AS stddev_credits
        FROM hourly_usage GROUP BY WAREHOUSE_NAME
    ),
    hourly_queries AS (
        SELECT WAREHOUSE_NAME, DATE_TRUNC('hour', START_TIME) AS usage_hour, COUNT(*) AS query_count
        FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY
        WHERE START_TIME >= DATEADD('day', -{cost_days}, CURRENT_TIMESTAMP())
          {wh_bare}
        GROUP BY WAREHOUSE_NAME, usage_hour
    ),
    warehouse_query_stats AS (
        SELECT WAREHOUSE_NAME, WAREHOUSE_SIZE, AVG(BYTES_SCANNED) AS avg_bytes_scanned, COUNT(*) AS query_count
        FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY
        WHERE START_TIME >= DATEADD('day', -{cost_days}, CURRENT_TIMESTAMP())
          AND EXECUTION_STATUS = 'SUCCESS'
          {wh_bare}
        GROUP BY WAREHOUSE_NAME, WAREHOUSE_SIZE
    ),
    workload_credits AS (
        SELECT COALESCE(NULLIF(qh.QUERY_TAG, ''), 'role: ' || qh.ROLE_NAME) AS workload_label,
               qh.WAREHOUSE_NAME, qh.ROLE_NAME, SUM(qa.CREDITS_ATTRIBUTED_COMPUTE) AS total_credits
        FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY qa
        JOIN SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY qh ON qa.QUERY_ID = qh.QUERY_ID
        WHERE qa.START_TIME >= DATEADD('day', -{cost_days}, CURRENT_TIMESTAMP())
          {wh_qh}
        GROUP BY workload_label, qh.WAREHOUSE_NAME, qh.ROLE_NAME
    ),
    workload_baseline AS (
        SELECT AVG(total_credits) AS avg_credits, STDDEV(total_credits) AS stddev_credits FROM workload_credits
    )
    -- 1. Long-running queries (top {cap} by elapsed time)
    SELECT 'performance' AS CATEGORY, 'long_running' AS SUBTYPE, qh.WAREHOUSE_NAME AS WAREHOUSE_NAME,
           qh.QUERY_ID AS QUERY_ID, qh.ROLE_NAME AS ROLE_NAME,
           CAST(qh.TOTAL_ELAPSED_TIME AS FLOAT) AS DETECTED_VALUE,
           CAST(NULL AS FLOAT) AS BASELINE_VALUE, CAST(NULL AS FLOAT) AS DEVIATION_PCT,
           LEFT(qh.QUERY_TEXT, {snip}) AS QUERY_TEXT, qh.START_TIME AS EVENT_TIME
    FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY qh
    WHERE qh.EXECUTION_STATUS IN ('SUCCESS', 'FAIL')
      AND qh.TOTAL_ELAPSED_TIME > {slow_threshold_ms}
      {select_clause}
      {wh_qh}
      AND qh.START_TIME >= DATEADD('hour', -{perf_hours}, CURRENT_TIMESTAMP())
    QUALIFY ROW_NUMBER() OVER (ORDER BY qh.TOTAL_ELAPSED_TIME DESC) <= {cap}
    UNION ALL
    -- 2. Cancelled queries (top {cap} most recent)
    SELECT 'performance', 'cancelled', qh.WAREHOUSE_NAME, qh.QUERY_ID, qh.ROLE_NAME,
           qh.TOTAL_ELAPSED_TIME, NULL, NULL, LEFT(qh.QUERY_TEXT, {snip}), qh.START_TIME
    FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY qh
    WHERE qh.EXECUTION_STATUS = 'FAIL' AND qh.ERROR_MESSAGE ILIKE '%cancel%'
      {wh_qh}
      AND qh.START_TIME >= DATEADD('hour', -{perf_hours}, CURRENT_TIMESTAMP())
    QUALIFY ROW_NUMBER() OVER (ORDER BY qh.START_TIME DESC) <= {cap}
    UNION ALL
    -- 3. General failures (top {cap} most recent)
    SELECT 'performance', 'failed', qh.WAREHOUSE_NAME, qh.QUERY_ID, qh.ROLE_NAME,
           qh.TOTAL_ELAPSED_TIME, NULL, NULL, LEFT(qh.QUERY_TEXT, {snip}), qh.START_TIME
    FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY qh
    WHERE qh.EXECUTION_STATUS = 'FAIL'
      AND (qh.ERROR_MESSAGE IS NULL OR qh.ERROR_MESSAGE NOT ILIKE '%cancel%')
      {wh_qh}
      AND qh.START_TIME >= DATEADD('hour', -{perf_hours}, CURRENT_TIMESTAMP())
    QUALIFY ROW_NUMBER() OVER (ORDER BY qh.START_TIME DESC) <= {cap}
    UNION ALL
    -- 4. Spilling queries (top {cap} by bytes spilled; baseline carries the REMOTE portion)
    SELECT 'performance', 'spilling', qh.WAREHOUSE_NAME, qh.QUERY_ID, qh.ROLE_NAME,
           qh.BYTES_SPILLED_TO_LOCAL_STORAGE + qh.BYTES_SPILLED_TO_REMOTE_STORAGE,
           qh.BYTES_SPILLED_TO_REMOTE_STORAGE, NULL, LEFT(qh.QUERY_TEXT, {snip}), qh.START_TIME
    FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY qh
    WHERE qh.START_TIME >= DATEADD('hour', -{perf_hours}, CURRENT_TIMESTAMP())
      AND (qh.BYTES_SPILLED_TO_LOCAL_STORAGE > 0 OR qh.BYTES_SPILLED_TO_REMOTE_STORAGE > 0)
      {wh_qh}
    QUALIFY ROW_NUMBER() OVER (ORDER BY (qh.BYTES_SPILLED_TO_LOCAL_STORAGE + qh.BYTES_SPILLED_TO_REMOTE_STORAGE) DESC) <= {cap}
    UNION ALL
    -- 5. Cost anomaly: hourly credit spike vs baseline (top {cap})
    SELECT 'cost', 'credit_spike', h.WAREHOUSE_NAME, NULL, NULL,
           h.credits, b.avg_credits,
           CASE WHEN b.avg_credits > 0 THEN ((h.credits - b.avg_credits) / b.avg_credits) * 100 ELSE NULL END,
           NULL, h.usage_hour
    FROM hourly_usage h
    JOIN wh_baseline b ON h.WAREHOUSE_NAME = b.WAREHOUSE_NAME
    WHERE b.stddev_credits > 0 AND h.credits > b.avg_credits + (2 * b.stddev_credits)
    QUALIFY ROW_NUMBER() OVER (ORDER BY h.credits DESC) <= {cap}
    UNION ALL
    -- 6. Idle-but-running (top {cap})
    SELECT 'cost', 'idle_running', hc.WAREHOUSE_NAME, NULL, NULL,
           hc.credits, COALESCE(hq.query_count, 0), NULL, NULL, hc.usage_hour
    FROM hourly_usage hc
    LEFT JOIN hourly_queries hq ON hc.WAREHOUSE_NAME = hq.WAREHOUSE_NAME AND hc.usage_hour = hq.usage_hour
    WHERE hc.credits > 0.01 AND COALESCE(hq.query_count, 0) = 0
    QUALIFY ROW_NUMBER() OVER (ORDER BY hc.credits DESC) <= {cap}
    UNION ALL
    -- 7. Oversized warehouse (top {cap})
    SELECT 'cost', 'oversized_warehouse', WAREHOUSE_NAME, NULL, NULL,
           avg_bytes_scanned, query_count, NULL, NULL, CURRENT_TIMESTAMP()
    FROM warehouse_query_stats
    WHERE WAREHOUSE_SIZE NOT IN ('X-Small', 'Small')  -- VERIFY exact casing in your account
      AND avg_bytes_scanned < 100000000
    QUALIFY ROW_NUMBER() OVER (ORDER BY query_count DESC) <= {cap}
    UNION ALL
    -- 8. Workload cost spike (top {cap})
    SELECT 'cost', 'workload_cost_spike', wc.WAREHOUSE_NAME, NULL, wc.ROLE_NAME,
           wc.total_credits, wb.avg_credits,
           CASE WHEN wb.avg_credits > 0 THEN ((wc.total_credits - wb.avg_credits) / wb.avg_credits) * 100 ELSE NULL END,
           NULL, CURRENT_TIMESTAMP()
    FROM workload_credits wc
    CROSS JOIN workload_baseline wb
    WHERE wb.stddev_credits > 0 AND wc.total_credits > wb.avg_credits + (2 * wb.stddev_credits)
    QUALIFY ROW_NUMBER() OVER (ORDER BY wc.total_credits DESC) <= {cap}
    UNION ALL
    -- 9. Queuing / warehouse contention (concurrency); detected = total queue ms, baseline = queued count
    SELECT 'performance', 'queuing', q.WAREHOUSE_NAME, NULL, NULL,
           q.total_overload_ms, q.queued_count, NULL, NULL, CURRENT_TIMESTAMP()
    FROM (
        SELECT qh.WAREHOUSE_NAME,
               SUM(qh.QUEUED_OVERLOAD_TIME) AS total_overload_ms,
               COUNT_IF(qh.QUEUED_OVERLOAD_TIME > 0) AS queued_count
        FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY qh
        WHERE qh.START_TIME >= DATEADD('hour', -{perf_hours}, CURRENT_TIMESTAMP())
          AND qh.WAREHOUSE_NAME IS NOT NULL
          {wh_qh}
        GROUP BY qh.WAREHOUSE_NAME
    ) q
    WHERE q.total_overload_ms > 60000
    QUALIFY ROW_NUMBER() OVER (ORDER BY q.total_overload_ms DESC) <= {cap}
    """
    return session.sql(sql).to_pandas()


try:
    raw_df = compute_flagged_events(perf_hours, cost_days, slow_threshold_s, select_only, warehouse_scope)
except Exception as e:
    st.error(
        "Could not read from `SNOWFLAKE.ACCOUNT_USAGE`. The role running this app needs "
        f"`IMPORTED PRIVILEGES` on the SNOWFLAKE database (granted by 01_setup.sql). Full error: {e}"
    )
    raw_df = pd.DataFrame(columns=[
        "CATEGORY", "SUBTYPE", "WAREHOUSE_NAME", "QUERY_ID", "ROLE_NAME",
        "DETECTED_VALUE", "BASELINE_VALUE", "DEVIATION_PCT", "QUERY_TEXT", "EVENT_TIME",
    ])

# --- Top metrics ---
st.markdown("---")
cost_help = "Includes: " + ", ".join(v["label"] for k, v in RULE_INFO.items() if v["category"] == "cost")
perf_help = "Includes: " + ", ".join(v["label"] for k, v in RULE_INFO.items() if v["category"] == "performance")

col1, col2, col3, col4 = st.columns(4)
col1.metric("Total Events", len(raw_df))
col2.metric("Cost Anomalies", len(raw_df[raw_df["CATEGORY"] == "cost"]), help=cost_help)
col3.metric("Performance Issues", len(raw_df[raw_df["CATEGORY"] == "performance"]), help=perf_help)
col4.metric("Distinct Warehouses", raw_df["WAREHOUSE_NAME"].nunique() if len(raw_df) > 0 else 0)

# --- Detector coverage: every one of the 8 rules, even when it found nothing ---
rule_counts = raw_df["SUBTYPE"].value_counts().to_dict() if len(raw_df) > 0 else {}

st.markdown("---")
st.subheader(f"Detector coverage — all {len(RULE_INFO)} rules active")
st.caption(
    f"Every scan runs all {len(RULE_INFO)} detectors. A count of **0** means that detector found nothing in the "
    "current window — not that it's disabled. Hover any card to see exactly what it checks."
)

st.markdown("**🏎️ Performance detectors**")
perf_rules = [k for k, v in RULE_INFO.items() if v["category"] == "performance"]
for col, subtype in zip(st.columns(len(perf_rules)), perf_rules):
    info = RULE_INFO[subtype]
    col.metric(f"{info['icon']} {info['label']}", int(rule_counts.get(subtype, 0)), help=info["desc"])

st.markdown("**💰 Cost detectors**")
cost_rules = [k for k, v in RULE_INFO.items() if v["category"] == "cost"]
for col, subtype in zip(st.columns(len(cost_rules)), cost_rules):
    info = RULE_INFO[subtype]
    col.metric(f"{info['icon']} {info['label']}", int(rule_counts.get(subtype, 0)), help=info["desc"])

# --- High-risk highlight: remote disk spill ---
spill_df = raw_df[raw_df["SUBTYPE"] == "spilling"]
remote_spills = spill_df[spill_df["BASELINE_VALUE"].fillna(0) > 0] if len(spill_df) > 0 else spill_df
local_only = len(spill_df) - len(remote_spills)
if len(remote_spills) > 0:
    st.error(
        f"⚠️ **{len(remote_spills)} quer{'y' if len(remote_spills) == 1 else 'ies'} spilled to REMOTE "
        f"storage — high risk.** Remote spill is far slower and costlier than local spill; fix these first "
        f"(size the warehouse up or reduce the data scanned). "
        f"{local_only} more spilled only to local disk (lower risk)."
    )
elif len(spill_df) > 0:
    st.info(f"💿 {len(spill_df)} quer{'y' if len(spill_df) == 1 else 'ies'} spilled to local disk (lower risk). "
            "No remote spill detected.")

# --- Filters ---
st.markdown("---")
st.subheader("Filter results")
st.caption(
    "These filters apply to the chart and the tables below. The **Detector coverage** grid above "
    f"always shows the full, unfiltered counts for all {len(RULE_INFO)} rules."
)
filter_col1, filter_col2, filter_col3 = st.columns(3)
with filter_col1:
    category_filter = st.selectbox("Category", ["All", "cost", "performance"])
with filter_col2:
    if category_filter != "All":
        subtype_pool = [k for k, v in RULE_INFO.items() if v["category"] == category_filter]
    else:
        subtype_pool = list(RULE_INFO.keys())
    subtype_filter = st.selectbox(
        "Subtype", ["All"] + subtype_pool,
        format_func=lambda k: "All" if k == "All" else f"{RULE_INFO[k]['icon']} {RULE_INFO[k]['label']}",
    )
with filter_col3:
    warehouse_options = ["All"] + sorted(raw_df["WAREHOUSE_NAME"].dropna().unique().tolist())
    warehouse_filter = st.selectbox("Warehouse", warehouse_options)

df = raw_df.copy()
if category_filter != "All":
    df = df[df["CATEGORY"] == category_filter]
if subtype_filter != "All":
    df = df[df["SUBTYPE"] == subtype_filter]
if warehouse_filter != "All":
    df = df[df["WAREHOUSE_NAME"] == warehouse_filter]


# --- Smart recommendation engine ---
def generate_recommendation(subtype, wh, val, query, dev_pct, baseline=None):
    query = str(query or "").upper()
    if subtype == "long_running":
        seconds = val / 1000 if pd.notna(val) else 0
        reasons = []
        if "SELECT *" in query:
            reasons.append("uses SELECT * — pull only needed columns")
        if "WHERE" not in query:
            reasons.append("no WHERE clause — add a filter to prune partitions")
        if "CROSS JOIN" in query:
            reasons.append("contains a CROSS JOIN — multiplies row counts")
        if "ORDER BY" in query and "LIMIT" not in query:
            reasons.append("sorts full result without LIMIT")
        if not reasons:
            reasons.append("check the query profile for scan size and join strategy")
        return f"Ran {seconds:.1f}s on {wh}. " + "; ".join(reasons) + "."
    elif subtype == "cancelled":
        seconds = val / 1000 if pd.notna(val) else 0
        return f"Cancelled after {seconds:.1f}s on {wh}. If recurring, redesign before running again."
    elif subtype == "failed":
        if "DOES NOT EXIST" in query or "NOT AUTHORIZED" in query:
            return f"Failed on {wh} — object doesn't exist or isn't authorized. Check names/grants."
        return f"Failed on {wh}. Review the error message for root cause."
    elif subtype == "spilling":
        total_mb = val / 1_000_000 if pd.notna(val) else 0
        remote_mb = baseline / 1_000_000 if pd.notna(baseline) else 0
        local_mb = max(total_mb - remote_mb, 0)
        if remote_mb > 0:
            return (f"⚠️ HIGH RISK: spilled ~{remote_mb:.1f}MB to REMOTE storage "
                    f"(+{local_mb:.1f}MB local) on {wh}. Remote spill is very slow and costly — "
                    f"size the warehouse up or cut the data scanned as a priority.")
        return f"Spilled ~{local_mb:.1f}MB to local disk on {wh}. Consider a larger warehouse or filtering earlier."
    elif subtype == "credit_spike":
        pct = dev_pct if pd.notna(dev_pct) else 0
        return f"{wh} used {pct:.0f}% more credits than usual. Check what ran in this window."
    elif subtype == "idle_running":
        return f"{wh} burned credits with zero queries. Check AUTO_SUSPEND setting."
    elif subtype == "oversized_warehouse":
        mb = val / 1_000_000 if pd.notna(val) else 0
        return f"{wh} averages ~{mb:.1f}MB scanned but is sized above Small. Consider downsizing."
    elif subtype == "workload_cost_spike":
        pct = dev_pct if pd.notna(dev_pct) else 0
        return f"This workload used {pct:.0f}% more credits than the typical workload. Review whether its logic or schedule changed recently."
    elif subtype == "queuing":
        secs = val / 1000 if pd.notna(val) else 0
        n = int(baseline) if pd.notna(baseline) else 0
        return (f"{n} quer{'y' if n == 1 else 'ies'} queued ~{secs:.0f}s total on {wh} — concurrency "
                f"contention. Consider a multi-cluster warehouse (scales out under load) rather than a bigger size.")
    return "Review manually."


# --- Deduplication engine ---
def dedupe_events(data):
    if len(data) == 0:
        return pd.DataFrame()
    data = data.copy()
    data["QUERY_TEXT"] = data["QUERY_TEXT"].fillna("(warehouse-level event — no specific query)")
    data["WAREHOUSE_NAME"] = data["WAREHOUSE_NAME"].fillna("N/A")
    data["ROLE_NAME"] = data["ROLE_NAME"].fillna("N/A")
    data["QUERY_ID"] = data["QUERY_ID"].fillna("")
    data["GROUP_KEY"] = data.apply(
        lambda r: r["QUERY_TEXT"] if r["QUERY_TEXT"].strip() != "" else f"{r['WAREHOUSE_NAME']}::{r['SUBTYPE']}",
        axis=1
    )
    grouped = data.groupby(["CATEGORY", "SUBTYPE", "GROUP_KEY"], as_index=False).agg(
        WAREHOUSE_NAME=("WAREHOUSE_NAME", "first"),
        ROLE_NAME=("ROLE_NAME", "first"),
        QUERY_ID=("QUERY_ID", "first"),
        DETECTED_VALUE=("DETECTED_VALUE", "max"),
        BASELINE_VALUE=("BASELINE_VALUE", "first"),
        DEVIATION_PCT=("DEVIATION_PCT", "max"),
        QUERY_TEXT=("QUERY_TEXT", "first"),
        OCCURRENCES=("EVENT_TIME", "count"),
        FIRST_SEEN=("EVENT_TIME", "min"),
        LAST_SEEN=("EVENT_TIME", "max"),
    )
    grouped["Recommendation"] = grouped.apply(
        lambda r: generate_recommendation(
            r["SUBTYPE"], r["WAREHOUSE_NAME"], r["DETECTED_VALUE"], r["QUERY_TEXT"],
            r["DEVIATION_PCT"], r["BASELINE_VALUE"]
        ),
        axis=1
    )
    return grouped.sort_values("LAST_SEEN", ascending=False)


deduped_df = dedupe_events(df)

# --- Chart: anomalies by detector (all 8 rules; reflects the current filter) ---
st.markdown("---")
st.subheader("Anomalies by detector")
st.caption(
    f"All {len(RULE_INFO)} rules are shown and the bars reflect your current filter — a bar at zero means that detector "
    "found nothing (or is filtered out). Each detector is capped at its **top 200 findings by severity**, "
    "a memory-safety limit that keeps the app fast on large accounts."
)
filtered_counts = df["SUBTYPE"].value_counts().to_dict() if len(df) > 0 else {}
chart_source = pd.DataFrame([
    {"DETECTOR": RULE_INFO[k]["label"], "CATEGORY": RULE_INFO[k]["category"],
     "count": int(filtered_counts.get(k, 0))}
    for k in RULE_INFO
])
color_scale = alt.Scale(domain=["cost", "performance"], range=["#ff6b6b", "#4ecdc4"])
chart = (
    alt.Chart(chart_source)
    .mark_bar(cornerRadiusTopLeft=6, cornerRadiusTopRight=6, opacity=0.9)
    .encode(
        x=alt.X("DETECTOR:N", title="Detector", sort="-y", axis=alt.Axis(labelAngle=-35, labelFontSize=11)),
        y=alt.Y("count:Q", title="Anomalies found"),
        color=alt.Color("CATEGORY:N", title="Category", scale=color_scale, legend=alt.Legend(orient="top-right")),
        tooltip=[alt.Tooltip("DETECTOR:N", title="Detector"), alt.Tooltip("CATEGORY:N", title="Category"),
                 alt.Tooltip("count:Q", title="Anomalies found")],
    )
    .properties(height=350).configure_axis(gridOpacity=0.3).configure_view(strokeWidth=0)
)
st.altair_chart(chart, use_container_width=True)

# --- Section: Cost attribution by workload ---
st.markdown("---")
st.subheader("Cost Attribution by Workload")
st.caption(
    "Attributes credit usage to the most specific signal available: an explicit QUERY_TAG, "
    "a detected dbt model, or the executing role as a fallback."
)


def extract_dbt_model(query_text):
    if not query_text:
        return None
    match = re.search(r'"resource_type":\s*"model".*?"name":\s*"([^"]+)"', query_text, re.DOTALL)
    return match.group(1) if match else None


def resolve_workload_label(query_tag, query_text, role_name):
    if query_tag and str(query_tag).strip() != "":
        return query_tag, "query_tag"
    dbt_model = extract_dbt_model(query_text)
    if dbt_model:
        return dbt_model, "dbt_model"
    return f"role: {role_name}" if role_name else "unattributed", "role_fallback"


@st.cache_data(ttl=300, show_spinner=False)
def load_attribution(cost_days: int, warehouse: str) -> pd.DataFrame:
    # Memory guards: truncate query text (dbt's comment sits at the start of the
    # query, so a 5000-char snippet keeps attribution working), scope to a single
    # warehouse when selected, and hard-cap the rows pulled into the app.
    wh_clause = ""
    if warehouse and warehouse != "All warehouses":
        wh_clause = "AND qh.WAREHOUSE_NAME = '" + warehouse.replace("'", "''") + "'"
    return session.sql(f"""
        SELECT qh.QUERY_TAG, LEFT(qh.QUERY_TEXT, 5000) AS QUERY_TEXT, qh.ROLE_NAME, qh.WAREHOUSE_NAME,
               qa.CREDITS_ATTRIBUTED_COMPUTE AS CREDITS
        FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY qa
        JOIN SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY qh ON qa.QUERY_ID = qh.QUERY_ID
        WHERE qa.START_TIME >= DATEADD('day', -{cost_days}, CURRENT_TIMESTAMP())
          {wh_clause}
        ORDER BY qa.CREDITS_ATTRIBUTED_COMPUTE DESC
        LIMIT 100000
    """).to_pandas()


try:
    attribution_raw = load_attribution(cost_days, warehouse_scope)
except Exception:
    attribution_raw = pd.DataFrame()

if len(attribution_raw) > 0:
    attribution_raw[["WORKLOAD_LABEL", "ATTRIBUTION_METHOD"]] = attribution_raw.apply(
        lambda r: pd.Series(resolve_workload_label(r["QUERY_TAG"], r["QUERY_TEXT"], r["ROLE_NAME"])), axis=1
    )
    total_credits = attribution_raw["CREDITS"].sum()
    tagged_pct = (
        attribution_raw.loc[attribution_raw["ATTRIBUTION_METHOD"] != "role_fallback", "CREDITS"].sum()
        / total_credits * 100 if total_credits > 0 else 0
    )
    if tagged_pct < 30:
        st.warning(
            f"Only {tagged_pct:.0f}% of credits are attributed via QUERY_TAG or dbt — most cost is falling "
            f"back to role-level attribution. Setting QUERY_TAG in your orchestration tool would sharpen this."
        )
    else:
        st.success(f"{tagged_pct:.0f}% of credits are attributed via explicit tags or dbt models.")

    workload_summary = (
        attribution_raw.groupby(["WORKLOAD_LABEL", "ATTRIBUTION_METHOD"], as_index=False)["CREDITS"]
        .sum().sort_values("CREDITS", ascending=False).head(15)
    )
    method_colors = alt.Scale(domain=["query_tag", "dbt_model", "role_fallback"],
                              range=["#4ecdc4", "#a78bfa", "#ff6b6b"])
    workload_chart = (
        alt.Chart(workload_summary)
        .mark_bar(cornerRadiusTopLeft=6, cornerRadiusTopRight=6, opacity=0.9)
        .encode(
            x=alt.X("CREDITS:Q", title=f"Credits ({cost_days} days)"),
            y=alt.Y("WORKLOAD_LABEL:N", title="", sort="-x"),
            color=alt.Color("ATTRIBUTION_METHOD:N", title="Attributed via", scale=method_colors),
            tooltip=[alt.Tooltip("WORKLOAD_LABEL:N", title="Workload"),
                     alt.Tooltip("ATTRIBUTION_METHOD:N", title="Method"),
                     alt.Tooltip("CREDITS:Q", title="Credits", format=".3f")],
        )
        .properties(height=400).configure_axis(gridOpacity=0.3).configure_view(strokeWidth=0)
    )
    st.altair_chart(workload_chart, use_container_width=True)
else:
    st.info(
        "No attribution data yet — QUERY_ATTRIBUTION_HISTORY can lag several hours, and this needs at "
        "least some completed queries in the selected window."
    )

# --- Section: What to do about it ---
st.markdown("---")
st.subheader("What To Do About It")
st.caption("Deduplicated by query — smart recommendations based on query pattern analysis. Use the Query ID to look up details in Query History.")
if len(deduped_df) > 0:
    display_df = deduped_df[[
        "SUBTYPE", "WAREHOUSE_NAME", "QUERY_ID", "OCCURRENCES", "LAST_SEEN", "QUERY_TEXT", "Recommendation"
    ]].copy()
    display_df.columns = ["Type", "Warehouse", "Query ID", "Count", "Last Seen", "Query", "Recommendation"]
    st.dataframe(
        display_df, use_container_width=True,
        column_config={
            "Query ID": st.column_config.TextColumn(width="small"),
            "Query": st.column_config.TextColumn(width="large"),
            "Recommendation": st.column_config.TextColumn(width="large"),
            "Count": st.column_config.NumberColumn(format="%d"),
        },
        hide_index=True,
    )
else:
    st.info("Nothing to recommend yet for this filter.")

# --- Section: Full Detail ---
st.markdown("---")
st.subheader("Full Detail")
with st.expander("View deduplicated events with occurrence count, baseline & deviation"):
    if len(deduped_df) > 0:
        detail_df = deduped_df[[
            "CATEGORY", "SUBTYPE", "WAREHOUSE_NAME", "QUERY_ID", "ROLE_NAME",
            "DETECTED_VALUE", "BASELINE_VALUE", "DEVIATION_PCT", "OCCURRENCES",
            "FIRST_SEEN", "LAST_SEEN", "QUERY_TEXT"
        ]].rename(columns={
            "CATEGORY": "Category", "SUBTYPE": "Type", "WAREHOUSE_NAME": "Warehouse", "QUERY_ID": "Query ID",
            "ROLE_NAME": "Role", "DETECTED_VALUE": "Peak Value", "BASELINE_VALUE": "Baseline",
            "DEVIATION_PCT": "Deviation %", "OCCURRENCES": "Count", "FIRST_SEEN": "First Seen",
            "LAST_SEEN": "Last Seen", "QUERY_TEXT": "Query",
        })
        st.dataframe(
            detail_df, use_container_width=True,
            column_config={
                "Query": st.column_config.TextColumn(width="large"),
                "Peak Value": st.column_config.NumberColumn(format="%.0f"),
                "Baseline": st.column_config.NumberColumn(format="%.2f"),
                "Deviation %": st.column_config.NumberColumn(format="%.1f%%"),
            },
            hide_index=True,
        )
        st.markdown(f"**Showing {len(detail_df)} unique issues** (from {len(df)} total events matching filters)")
    else:
        st.caption("No data to display.")

# --- Footer ---
st.markdown("---")
st.info(
    "**Looking for sizing recommendations?** The companion **Warehouse Sizing Advisor** app "
    "(`SNOWLENS_SIZING_ADVISOR`, under Projects > Streamlit) analyses spill, queuing, scan volume "
    "and concurrency per warehouse, then recommends a size and cluster count with ready-to-review "
    "`ALTER WAREHOUSE` statements."
)
st.caption(f"SnowLens · Questions or feedback? {CONTACT_EMAIL}")
