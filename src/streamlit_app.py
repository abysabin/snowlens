"""
SnowLens — Full Edition
Snowflake cost & performance observability, without leaving Snowflake.

All 8 detectors, configurable detection windows, and cost attribution by
workload. Like the Trial edition, everything is computed live in Python from
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

RULE_INFO = {
    "long_running":        {"label": "Slow Query Finder",                  "icon": "🐢", "category": "performance",
                            "desc": "A query took longer than 10 seconds of total elapsed time."},
    "cancelled":           {"label": "Cancelled Query Detector",           "icon": "🚫", "category": "performance",
                            "desc": "A query was manually stopped or cancelled by a timeout."},
    "failed":              {"label": "Query Failure Detector",             "icon": "⚠️", "category": "performance",
                            "desc": "A query errored out for a reason other than cancellation."},
    "spilling":            {"label": "Disk Spill Detector",                "icon": "💿", "category": "performance",
                            "desc": "A query ran out of memory and spilled to local or remote disk."},
    "credit_spike":        {"label": "Cost Anomaly Detector",             "icon": "💸", "category": "cost",
                            "desc": "A warehouse's hourly credit usage spiked more than 2σ above its own average."},
    "idle_running":        {"label": "Idle-But-Running Detector",          "icon": "😴", "category": "cost",
                            "desc": "A warehouse burned credits with zero queries running."},
    "oversized_warehouse": {"label": "Oversized Warehouse Detector",       "icon": "📏", "category": "cost",
                            "desc": "A warehouse is sized larger than its workload needs."},
    "workload_cost_spike": {"label": "Workload / Role Cost Spike Detector", "icon": "👤", "category": "cost",
                            "desc": "A tagged workload (or role, if untagged) is a cost outlier vs other workloads."},
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
        st.markdown("**What each type means**")
        for subtype, info in RULE_INFO.items():
            st.markdown(f"- {info['icon']} **{info['label']}** (`{subtype}`) — {info['desc']}")
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
        st.markdown("---")
        st.caption(f"Questions or feedback? {CONTACT_EMAIL}")

# --- Detection window controls (Full Edition) ---
st.markdown("---")
with st.expander("⚙️ Detection window settings", expanded=False):
    st.caption(
        "Trial is fixed at 6h (performance) / 7d (cost). Full Edition lets you widen either window — "
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

perf_hours = int(perf_hours)
cost_days = int(cost_days)


# --- Live detection: compute all 8 rules from ACCOUNT_USAGE, like the trial ---
@st.cache_data(ttl=300, show_spinner="Scanning your account…")
def compute_flagged_events(perf_hours: int, cost_days: int) -> pd.DataFrame:
    sql = f"""
    WITH hourly_usage AS (
        SELECT WAREHOUSE_NAME, DATE_TRUNC('hour', START_TIME) AS usage_hour, SUM(CREDITS_USED) AS credits
        FROM SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY
        WHERE START_TIME >= DATEADD('day', -{cost_days}, CURRENT_TIMESTAMP())
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
        GROUP BY WAREHOUSE_NAME, usage_hour
    ),
    warehouse_query_stats AS (
        SELECT WAREHOUSE_NAME, WAREHOUSE_SIZE, AVG(BYTES_SCANNED) AS avg_bytes_scanned, COUNT(*) AS query_count
        FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY
        WHERE START_TIME >= DATEADD('day', -{cost_days}, CURRENT_TIMESTAMP())
          AND EXECUTION_STATUS = 'SUCCESS'
        GROUP BY WAREHOUSE_NAME, WAREHOUSE_SIZE
    ),
    workload_credits AS (
        SELECT COALESCE(NULLIF(qh.QUERY_TAG, ''), 'role: ' || qh.ROLE_NAME) AS workload_label,
               qh.WAREHOUSE_NAME, qh.ROLE_NAME, SUM(qa.CREDITS_ATTRIBUTED_COMPUTE) AS total_credits
        FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY qa
        JOIN SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY qh ON qa.QUERY_ID = qh.QUERY_ID
        WHERE qa.START_TIME >= DATEADD('day', -{cost_days}, CURRENT_TIMESTAMP())
        GROUP BY workload_label, qh.WAREHOUSE_NAME, qh.ROLE_NAME
    ),
    workload_baseline AS (
        SELECT AVG(total_credits) AS avg_credits, STDDEV(total_credits) AS stddev_credits FROM workload_credits
    )
    -- 1. Long-running queries
    SELECT 'performance' AS CATEGORY, 'long_running' AS SUBTYPE, qh.WAREHOUSE_NAME AS WAREHOUSE_NAME,
           qh.QUERY_ID AS QUERY_ID, qh.ROLE_NAME AS ROLE_NAME,
           CAST(qh.TOTAL_ELAPSED_TIME AS FLOAT) AS DETECTED_VALUE,
           CAST(NULL AS FLOAT) AS BASELINE_VALUE, CAST(NULL AS FLOAT) AS DEVIATION_PCT,
           qh.QUERY_TEXT AS QUERY_TEXT, qh.START_TIME AS EVENT_TIME
    FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY qh
    WHERE qh.EXECUTION_STATUS IN ('SUCCESS', 'FAIL')
      AND qh.TOTAL_ELAPSED_TIME > 10000
      AND qh.START_TIME >= DATEADD('hour', -{perf_hours}, CURRENT_TIMESTAMP())
    UNION ALL
    -- 2. Cancelled queries
    SELECT 'performance', 'cancelled', qh.WAREHOUSE_NAME, qh.QUERY_ID, qh.ROLE_NAME,
           qh.TOTAL_ELAPSED_TIME, NULL, NULL, qh.QUERY_TEXT, qh.START_TIME
    FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY qh
    WHERE qh.EXECUTION_STATUS = 'FAIL' AND qh.ERROR_MESSAGE ILIKE '%cancel%'
      AND qh.START_TIME >= DATEADD('hour', -{perf_hours}, CURRENT_TIMESTAMP())
    UNION ALL
    -- 3. General failures
    SELECT 'performance', 'failed', qh.WAREHOUSE_NAME, qh.QUERY_ID, qh.ROLE_NAME,
           qh.TOTAL_ELAPSED_TIME, NULL, NULL, qh.QUERY_TEXT, qh.START_TIME
    FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY qh
    WHERE qh.EXECUTION_STATUS = 'FAIL'
      AND (qh.ERROR_MESSAGE IS NULL OR qh.ERROR_MESSAGE NOT ILIKE '%cancel%')
      AND qh.START_TIME >= DATEADD('hour', -{perf_hours}, CURRENT_TIMESTAMP())
    UNION ALL
    -- 4. Spilling queries
    SELECT 'performance', 'spilling', qh.WAREHOUSE_NAME, qh.QUERY_ID, qh.ROLE_NAME,
           qh.BYTES_SPILLED_TO_LOCAL_STORAGE + qh.BYTES_SPILLED_TO_REMOTE_STORAGE,
           NULL, NULL, qh.QUERY_TEXT, qh.START_TIME
    FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY qh
    WHERE qh.START_TIME >= DATEADD('hour', -{perf_hours}, CURRENT_TIMESTAMP())
      AND (qh.BYTES_SPILLED_TO_LOCAL_STORAGE > 0 OR qh.BYTES_SPILLED_TO_REMOTE_STORAGE > 0)
    UNION ALL
    -- 5. Cost anomaly: hourly credit spike vs baseline
    SELECT 'cost', 'credit_spike', h.WAREHOUSE_NAME, NULL, NULL,
           h.credits, b.avg_credits,
           CASE WHEN b.avg_credits > 0 THEN ((h.credits - b.avg_credits) / b.avg_credits) * 100 ELSE NULL END,
           NULL, h.usage_hour
    FROM hourly_usage h
    JOIN wh_baseline b ON h.WAREHOUSE_NAME = b.WAREHOUSE_NAME
    WHERE b.stddev_credits > 0 AND h.credits > b.avg_credits + (2 * b.stddev_credits)
    UNION ALL
    -- 6. Idle-but-running
    SELECT 'cost', 'idle_running', hc.WAREHOUSE_NAME, NULL, NULL,
           hc.credits, COALESCE(hq.query_count, 0), NULL, NULL, hc.usage_hour
    FROM hourly_usage hc
    LEFT JOIN hourly_queries hq ON hc.WAREHOUSE_NAME = hq.WAREHOUSE_NAME AND hc.usage_hour = hq.usage_hour
    WHERE hc.credits > 0.01 AND COALESCE(hq.query_count, 0) = 0
    UNION ALL
    -- 7. Oversized warehouse
    SELECT 'cost', 'oversized_warehouse', WAREHOUSE_NAME, NULL, NULL,
           avg_bytes_scanned, query_count, NULL, NULL, CURRENT_TIMESTAMP()
    FROM warehouse_query_stats
    WHERE WAREHOUSE_SIZE NOT IN ('X-Small', 'Small')  -- VERIFY exact casing in your account
      AND avg_bytes_scanned < 100000000
    UNION ALL
    -- 8. Workload cost spike
    SELECT 'cost', 'workload_cost_spike', wc.WAREHOUSE_NAME, NULL, wc.ROLE_NAME,
           wc.total_credits, wb.avg_credits,
           CASE WHEN wb.avg_credits > 0 THEN ((wc.total_credits - wb.avg_credits) / wb.avg_credits) * 100 ELSE NULL END,
           NULL, CURRENT_TIMESTAMP()
    FROM workload_credits wc
    CROSS JOIN workload_baseline wb
    WHERE wb.stddev_credits > 0 AND wc.total_credits > wb.avg_credits + (2 * wb.stddev_credits)
    """
    return session.sql(sql).to_pandas()


try:
    raw_df = compute_flagged_events(perf_hours, cost_days)
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

# --- Filters ---
st.markdown("---")
filter_col1, filter_col2, filter_col3 = st.columns(3)
with filter_col1:
    category_filter = st.selectbox("Category", ["All", "cost", "performance"])
with filter_col2:
    subtype_options = ["All"] + sorted(raw_df["SUBTYPE"].dropna().unique().tolist())
    subtype_filter = st.selectbox("Subtype", subtype_options)
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
def generate_recommendation(subtype, wh, val, query, dev_pct):
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
        mb = val / 1_000_000 if pd.notna(val) else 0
        return f"Spilled ~{mb:.1f}MB on {wh}. Try a larger warehouse or filter data earlier."
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
            r["SUBTYPE"], r["WAREHOUSE_NAME"], r["DETECTED_VALUE"], r["QUERY_TEXT"], r["DEVIATION_PCT"]
        ),
        axis=1
    )
    return grouped.sort_values("LAST_SEEN", ascending=False)


deduped_df = dedupe_events(df)

# --- Chart: Flagged events by subtype ---
st.markdown("---")
st.subheader("Flagged Events by Subtype")
if len(df) > 0:
    chart_data = df.groupby(["SUBTYPE", "CATEGORY"]).size().reset_index(name="count")
    color_scale = alt.Scale(domain=["cost", "performance"], range=["#ff6b6b", "#4ecdc4"])
    chart = (
        alt.Chart(chart_data)
        .mark_bar(cornerRadiusTopLeft=6, cornerRadiusTopRight=6, opacity=0.9)
        .encode(
            x=alt.X("SUBTYPE:N", title="Subtype", sort="-y", axis=alt.Axis(labelAngle=-30, labelFontSize=12)),
            y=alt.Y("count:Q", title="Event Count"),
            color=alt.Color("CATEGORY:N", title="Category", scale=color_scale, legend=alt.Legend(orient="top-right")),
            tooltip=[alt.Tooltip("SUBTYPE:N", title="Subtype"), alt.Tooltip("CATEGORY:N", title="Category"),
                     alt.Tooltip("count:Q", title="Count")],
        )
        .properties(height=350).configure_axis(gridOpacity=0.3).configure_view(strokeWidth=0)
    )
    st.altair_chart(chart, use_container_width=True)
else:
    st.info("No flagged events match the current filters.")

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
def load_attribution(cost_days: int) -> pd.DataFrame:
    return session.sql(f"""
        SELECT qh.QUERY_TAG, qh.QUERY_TEXT, qh.ROLE_NAME, qh.WAREHOUSE_NAME,
               qa.CREDITS_ATTRIBUTED_COMPUTE AS CREDITS
        FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY qa
        JOIN SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY qh ON qa.QUERY_ID = qh.QUERY_ID
        WHERE qa.START_TIME >= DATEADD('day', -{cost_days}, CURRENT_TIMESTAMP())
    """).to_pandas()


try:
    attribution_raw = load_attribution(cost_days)
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
st.caption(f"SnowLens Full Edition · Questions or feedback? {CONTACT_EMAIL}")
