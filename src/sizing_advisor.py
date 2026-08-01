"""
SnowLens — Warehouse Sizing Advisor
Data-driven Snowflake warehouse sizing recommendations.

Reads SNOWFLAKE.ACCOUNT_USAGE (QUERY_HISTORY, WAREHOUSE_METERING_HISTORY) and
derives a per-warehouse recommendation from observed behaviour: spill, queuing,
bytes scanned, concurrency, and credit burn. Nothing is stored, nothing leaves
your account, and no outbound network calls are made.

Companion app to SnowLens (SNOWLENS_APP).
"""

import streamlit as st
import pandas as pd
import altair as alt
from snowflake.snowpark.context import get_active_session

st.set_page_config(page_title="SnowLens — Warehouse Sizing Advisor", page_icon="📏", layout="wide")
session = get_active_session()

CONTACT_EMAIL = "vizcanvas@gmail.com"

DEFAULT_LOOKBACK_DAYS = 30
LOOKBACK_OPTIONS = [7, 30, 90]

# Snowflake warehouse size ladder. Credits per hour double at each step.
SIZE_LADDER = [
    ("X-Small", 1), ("Small", 2), ("Medium", 4), ("Large", 8),
    ("X-Large", 16), ("2X-Large", 32), ("3X-Large", 64), ("4X-Large", 128),
]
SIZE_NAMES = [s for s, _ in SIZE_LADDER]
SIZE_CREDITS = dict(SIZE_LADDER)
SIZE_SQL = {
    "X-Small": "XSMALL", "Small": "SMALL", "Medium": "MEDIUM", "Large": "LARGE",
    "X-Large": "XLARGE", "2X-Large": "XXLARGE", "3X-Large": "XXXLARGE", "4X-Large": "X4LARGE",
}

# Thresholds that drive the recommendation. Documented in the Help popover so
# users can reason about (and disagree with) every number.
REMOTE_SPILL_PCT_UP = 2.0      # >2% of queries spilling remotely -> size up
LOCAL_SPILL_PCT_UP = 15.0      # >15% spilling locally -> size up
DOWNSIZE_MB = 100.0            # p90 scan below this (and above Small) -> size down
DOWNSIZE_MAX_SPILL_PCT = 1.0   # ...but only if spill is essentially absent
QUEUE_SECONDS_MULTI = 60.0     # >60s cumulative queue -> multi-cluster
IDLE_CREDIT_FLOOR = 0.01       # credits in an hour with zero queries


st.title("Warehouse Sizing Advisor")
st.caption(f"SnowLens · sizing derived from your own ACCOUNT_USAGE history · Questions? {CONTACT_EMAIL}")

with st.popover("How these recommendations are derived"):
    st.markdown("**Signals read from ACCOUNT_USAGE**")
    st.markdown(
        "- **Remote spill rate** — share of queries writing to remote storage. The single strongest "
        "signal a warehouse is undersized; remote spill is dramatically slower than memory or local SSD.\n"
        "- **Local spill rate** — share spilling to local SSD. A softer signal, but sustained local spill "
        "still costs time.\n"
        "- **p90 bytes scanned** — the 90th-percentile scan per query. Used instead of the mean so a few "
        "large queries don't mask an otherwise small workload.\n"
        "- **Cumulative queue time** — `QUEUED_OVERLOAD_TIME` summed per warehouse. Signals **concurrency** "
        "pressure, which is fixed by scaling *out* (more clusters), not *up* (a bigger size).\n"
        "- **Peak concurrency** — the busiest hour's query count, used to size the cluster range.\n"
        "- **Idle credit hours** — hours that burned credits with zero queries, which points at AUTO_SUSPEND."
    )
    st.markdown("**Decision logic**")
    st.markdown(
        f"1. **Size up** if remote spill > {REMOTE_SPILL_PCT_UP}% of queries, or local spill > {LOCAL_SPILL_PCT_UP}%.\n"
        f"2. **Size down** if p90 scan < {DOWNSIZE_MB:.0f} MB, the warehouse is above Small, and spill is "
        f"under {DOWNSIZE_MAX_SPILL_PCT}%.\n"
        f"3. **Add clusters** (independently of size) if cumulative queue time > {QUEUE_SECONDS_MULTI:.0f}s.\n"
        "4. **Otherwise keep** the current size — the evidence doesn't justify a change."
    )
    st.markdown("**Important caveats**")
    st.markdown(
        "- Sizing is a starting point, not a verdict. Change one size at a time and re-measure.\n"
        "- A warehouse with very few queries in the window produces a weak signal — the table flags "
        "low-confidence rows.\n"
        "- `WAREHOUSE_SIZE` is read from QUERY_HISTORY, so it reflects the size queries actually ran on. "
        "If you resized mid-window, the most recent size is used.\n"
        "- ACCOUNT_USAGE lags from minutes to a few hours."
    )
    st.caption(f"Questions or feedback? {CONTACT_EMAIL}")

st.markdown("---")
c1, c2 = st.columns([1, 3])
with c1:
    lookback_days = st.selectbox(
        "Analysis window (days)", LOOKBACK_OPTIONS,
        index=LOOKBACK_OPTIONS.index(DEFAULT_LOOKBACK_DAYS),
        help="Longer windows give a more reliable signal but take longer to scan.",
    )
with c2:
    min_queries = st.slider(
        "Minimum queries to rate a warehouse", min_value=10, max_value=1000, value=50, step=10,
        help="Warehouses with fewer queries than this in the window are shown but marked low confidence.",
    )
lookback_days = int(lookback_days)


@st.cache_data(ttl=300, show_spinner="Reading your warehouse history…")
def load_warehouse_stats(days: int) -> pd.DataFrame:
    sql = f"""
    WITH q AS (
        SELECT
            WAREHOUSE_NAME,
            WAREHOUSE_SIZE,
            START_TIME,
            BYTES_SCANNED,
            QUEUED_OVERLOAD_TIME,
            BYTES_SPILLED_TO_LOCAL_STORAGE  AS local_spill,
            BYTES_SPILLED_TO_REMOTE_STORAGE AS remote_spill,
            TOTAL_ELAPSED_TIME
        FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY
        WHERE START_TIME >= DATEADD('day', -{days}, CURRENT_TIMESTAMP())
          AND WAREHOUSE_NAME IS NOT NULL
          AND WAREHOUSE_SIZE IS NOT NULL
          AND EXECUTION_STATUS = 'SUCCESS'
    ),
    latest_size AS (
        SELECT WAREHOUSE_NAME, WAREHOUSE_SIZE
        FROM q
        QUALIFY ROW_NUMBER() OVER (PARTITION BY WAREHOUSE_NAME ORDER BY START_TIME DESC) = 1
    ),
    per_wh AS (
        SELECT
            WAREHOUSE_NAME,
            COUNT(*)                                              AS query_count,
            APPROX_PERCENTILE(BYTES_SCANNED, 0.90)                AS p90_bytes_scanned,
            AVG(BYTES_SCANNED)                                    AS avg_bytes_scanned,
            APPROX_PERCENTILE(TOTAL_ELAPSED_TIME, 0.90)           AS p90_elapsed_ms,
            SUM(QUEUED_OVERLOAD_TIME)                             AS total_queue_ms,
            COUNT_IF(QUEUED_OVERLOAD_TIME > 0)                    AS queued_query_count,
            COUNT_IF(remote_spill > 0)                            AS remote_spill_queries,
            COUNT_IF(local_spill > 0 AND remote_spill = 0)        AS local_spill_queries,
            SUM(remote_spill)                                     AS remote_spill_bytes,
            SUM(local_spill)                                      AS local_spill_bytes
        FROM q
        GROUP BY WAREHOUSE_NAME
    ),
    hourly AS (
        SELECT WAREHOUSE_NAME, DATE_TRUNC('hour', START_TIME) AS h, COUNT(*) AS c
        FROM q
        GROUP BY WAREHOUSE_NAME, DATE_TRUNC('hour', START_TIME)
    ),
    peak AS (
        SELECT WAREHOUSE_NAME, MAX(c) AS peak_hourly_queries, AVG(c) AS avg_hourly_queries
        FROM hourly GROUP BY WAREHOUSE_NAME
    ),
    metering AS (
        SELECT WAREHOUSE_NAME, SUM(CREDITS_USED) AS total_credits
        FROM SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY
        WHERE START_TIME >= DATEADD('day', -{days}, CURRENT_TIMESTAMP())
          AND WAREHOUSE_NAME IS NOT NULL
        GROUP BY WAREHOUSE_NAME
    ),
    metered_hourly AS (
        SELECT WAREHOUSE_NAME, DATE_TRUNC('hour', START_TIME) AS h, SUM(CREDITS_USED) AS credits
        FROM SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY
        WHERE START_TIME >= DATEADD('day', -{days}, CURRENT_TIMESTAMP())
          AND WAREHOUSE_NAME IS NOT NULL
        GROUP BY WAREHOUSE_NAME, DATE_TRUNC('hour', START_TIME)
    ),
    idle AS (
        SELECT m.WAREHOUSE_NAME,
               COUNT_IF(m.credits > {IDLE_CREDIT_FLOOR} AND COALESCE(h.c, 0) = 0) AS idle_hours,
               SUM(CASE WHEN m.credits > {IDLE_CREDIT_FLOOR} AND COALESCE(h.c, 0) = 0
                        THEN m.credits ELSE 0 END) AS idle_credits
        FROM metered_hourly m
        LEFT JOIN hourly h ON m.WAREHOUSE_NAME = h.WAREHOUSE_NAME AND m.h = h.h
        GROUP BY m.WAREHOUSE_NAME
    )
    SELECT
        p.WAREHOUSE_NAME,
        s.WAREHOUSE_SIZE                       AS CURRENT_SIZE,
        p.query_count                          AS QUERY_COUNT,
        p.p90_bytes_scanned                    AS P90_BYTES_SCANNED,
        p.avg_bytes_scanned                    AS AVG_BYTES_SCANNED,
        p.p90_elapsed_ms                       AS P90_ELAPSED_MS,
        p.total_queue_ms                       AS TOTAL_QUEUE_MS,
        p.queued_query_count                   AS QUEUED_QUERY_COUNT,
        p.remote_spill_queries                 AS REMOTE_SPILL_QUERIES,
        p.local_spill_queries                  AS LOCAL_SPILL_QUERIES,
        p.remote_spill_bytes                   AS REMOTE_SPILL_BYTES,
        p.local_spill_bytes                    AS LOCAL_SPILL_BYTES,
        COALESCE(k.peak_hourly_queries, 0)     AS PEAK_HOURLY_QUERIES,
        COALESCE(k.avg_hourly_queries, 0)      AS AVG_HOURLY_QUERIES,
        COALESCE(m.total_credits, 0)           AS TOTAL_CREDITS,
        COALESCE(i.idle_hours, 0)              AS IDLE_HOURS,
        COALESCE(i.idle_credits, 0)            AS IDLE_CREDITS
    FROM per_wh p
    JOIN latest_size s ON p.WAREHOUSE_NAME = s.WAREHOUSE_NAME
    LEFT JOIN peak    k ON p.WAREHOUSE_NAME = k.WAREHOUSE_NAME
    LEFT JOIN metering m ON p.WAREHOUSE_NAME = m.WAREHOUSE_NAME
    LEFT JOIN idle    i ON p.WAREHOUSE_NAME = i.WAREHOUSE_NAME
    ORDER BY COALESCE(m.total_credits, 0) DESC
    LIMIT 500
    """
    return session.sql(sql).to_pandas()


def normalise_size(raw):
    """Map Snowflake's WAREHOUSE_SIZE text onto our ladder, tolerating casing variants."""
    if not raw:
        return None
    key = str(raw).strip().lower().replace("_", "-").replace(" ", "-")
    aliases = {
        "x-small": "X-Small", "xsmall": "X-Small",
        "small": "Small",
        "medium": "Medium",
        "large": "Large",
        "x-large": "X-Large", "xlarge": "X-Large",
        "2x-large": "2X-Large", "xxlarge": "2X-Large", "2xlarge": "2X-Large",
        "3x-large": "3X-Large", "xxxlarge": "3X-Large", "3xlarge": "3X-Large",
        "4x-large": "4X-Large", "x4large": "4X-Large", "4xlarge": "4X-Large",
    }
    return aliases.get(key)


def cluster_plan(peak_hourly, queue_seconds):
    """Cluster range is driven by concurrency pressure, not by data volume."""
    if queue_seconds <= QUEUE_SECONDS_MULTI:
        return 1, 1
    if peak_hourly <= 100:
        return 1, 3
    if peak_hourly <= 500:
        return 1, 6
    return 2, 10


def advise(row, min_queries):
    current = normalise_size(row["CURRENT_SIZE"])
    qc = int(row["QUERY_COUNT"] or 0)
    p90_mb = (row["P90_BYTES_SCANNED"] or 0) / 1_000_000
    remote_q = int(row["REMOTE_SPILL_QUERIES"] or 0)
    local_q = int(row["LOCAL_SPILL_QUERIES"] or 0)
    queue_s = (row["TOTAL_QUEUE_MS"] or 0) / 1000
    peak_hourly = int(row["PEAK_HOURLY_QUERIES"] or 0)
    idle_hours = int(row["IDLE_HOURS"] or 0)
    idle_credits = float(row["IDLE_CREDITS"] or 0)

    remote_pct = (remote_q / qc * 100) if qc else 0
    local_pct = (local_q / qc * 100) if qc else 0

    if current is None:
        return pd.Series({
            "RECOMMENDED_SIZE": "unknown", "ACTION": "review",
            "CONFIDENCE": "low", "REASONS": "Unrecognised warehouse size — review manually.",
            "MIN_CLUSTERS": 1, "MAX_CLUSTERS": 1,
            "REMOTE_SPILL_PCT": remote_pct, "LOCAL_SPILL_PCT": local_pct,
            "QUEUE_SECONDS": queue_s, "P90_MB": p90_mb, "EST_CREDIT_DELTA": 0.0,
        })

    idx = SIZE_NAMES.index(current)
    new_idx = idx
    reasons = []
    action = "keep"

    if qc == 0:
        # No successful queries in the window — nothing to reason from.
        reasons.append("No successful queries in this window, so no sizing signal is available.")
    elif remote_pct > REMOTE_SPILL_PCT_UP:
        new_idx = min(idx + 1, len(SIZE_NAMES) - 1)
        action = "size up"
        reasons.append(
            f"{remote_pct:.1f}% of queries spilled to remote storage "
            f"({remote_q} of {qc}) — the strongest undersizing signal."
        )
    elif local_pct > LOCAL_SPILL_PCT_UP:
        new_idx = min(idx + 1, len(SIZE_NAMES) - 1)
        action = "size up"
        reasons.append(
            f"{local_pct:.1f}% of queries spilled to local disk ({local_q} of {qc}) — "
            f"sustained memory pressure."
        )
    elif (p90_mb < DOWNSIZE_MB and idx > 1
          and remote_pct < DOWNSIZE_MAX_SPILL_PCT and local_pct < DOWNSIZE_MAX_SPILL_PCT):
        new_idx = max(idx - 1, 1)
        action = "size down"
        reasons.append(
            f"p90 scan is only {p90_mb:.1f} MB with negligible spill — the workload is small "
            f"relative to a {current} warehouse."
        )
    else:
        reasons.append(
            f"p90 scan {p90_mb:.1f} MB, remote spill {remote_pct:.1f}%, local spill {local_pct:.1f}% — "
            f"nothing here justifies a size change."
        )

    # Already at the top of the ladder: the signal is real but a size change can't fix it.
    if action == "size up" and new_idx == idx:
        action = "keep"
        reasons.append(
            f"{current} is the largest size this advisor recommends — the remaining fix is reducing "
            f"data scanned (better pruning, clustering keys, or splitting the query), not more compute."
        )

    min_c, max_c = cluster_plan(peak_hourly, queue_s)
    if max_c > 1:
        reasons.append(
            f"{queue_s:.0f}s cumulative queue time at up to {peak_hourly} queries/hour — "
            f"this is concurrency pressure, so scale out to {min_c}-{max_c} clusters rather than up."
        )
        if action == "keep":
            action = "add clusters"

    if idle_hours > 0:
        reasons.append(
            f"{idle_hours} hour(s) burned {idle_credits:.2f} credits with zero queries — "
            f"lower AUTO_SUSPEND."
        )

    recommended = SIZE_NAMES[new_idx]
    credit_delta = SIZE_CREDITS[recommended] - SIZE_CREDITS[current]

    if qc < min_queries:
        confidence = "low"
        reasons.append(f"Only {qc} queries in the window — treat this as a weak signal.")
    elif qc < min_queries * 5:
        confidence = "medium"
    else:
        confidence = "high"

    return pd.Series({
        "RECOMMENDED_SIZE": recommended, "ACTION": action, "CONFIDENCE": confidence,
        "REASONS": " ".join(reasons), "MIN_CLUSTERS": min_c, "MAX_CLUSTERS": max_c,
        "REMOTE_SPILL_PCT": remote_pct, "LOCAL_SPILL_PCT": local_pct,
        "QUEUE_SECONDS": queue_s, "P90_MB": p90_mb, "EST_CREDIT_DELTA": float(credit_delta),
    })


try:
    stats = load_warehouse_stats(lookback_days)
except Exception as e:
    st.error(
        "Could not read from `SNOWFLAKE.ACCOUNT_USAGE`. The role running this app needs "
        f"`IMPORTED PRIVILEGES` on the SNOWFLAKE database (granted by 01_setup.sql). Full error: {e}"
    )
    stats = pd.DataFrame()

if len(stats) == 0:
    st.info(
        "No warehouse activity found in this window. ACCOUNT_USAGE can lag several hours on a new "
        "account — try a longer window, or check back later."
    )
    st.stop()

advice = stats.join(stats.apply(lambda r: advise(r, min_queries), axis=1))

# --- Summary metrics ---
st.markdown("---")
up = int((advice["ACTION"] == "size up").sum())
down = int((advice["ACTION"] == "size down").sum())
clusters = int((advice["ACTION"] == "add clusters").sum())
keep = int((advice["ACTION"] == "keep").sum())

m1, m2, m3, m4 = st.columns(4)
m1.metric("Warehouses analysed", len(advice))
m2.metric("Recommend sizing up", up, help="Spill indicates these are undersized for their workload.")
m3.metric("Recommend sizing down", down, help="Scan volume is small relative to the current size.")
m4.metric("Need more clusters", clusters, help="Queuing indicates concurrency pressure, not size pressure.")

if down > 0:
    savings = -advice.loc[advice["ACTION"] == "size down", "EST_CREDIT_DELTA"].sum()
    st.success(
        f"Downsizing the {down} flagged warehouse(s) would cut roughly **{savings:.0f} credits/hour** "
        f"of peak capacity cost. Actual savings depend on how many hours they run."
    )
if up > 0:
    added = advice.loc[advice["ACTION"] == "size up", "EST_CREDIT_DELTA"].sum()
    st.warning(
        f"Sizing up the {up} flagged warehouse(s) adds roughly **{added:.0f} credits/hour** of peak "
        f"capacity — but spill usually makes queries run long enough that the larger size costs "
        f"less in total. Measure before and after."
    )

# --- Recommendation table ---
st.markdown("---")
st.subheader("Per-warehouse recommendations")
st.caption(
    "Sorted by credit consumption — the warehouses at the top are where a sizing change matters most. "
    "Confidence reflects how many queries backed the recommendation."
)

action_filter = st.multiselect(
    "Show actions", ["size up", "size down", "add clusters", "keep", "review"],
    default=["size up", "size down", "add clusters"],
)
view = advice[advice["ACTION"].isin(action_filter)] if action_filter else advice

if len(view) == 0:
    st.info("No warehouses match the selected actions.")
else:
    table = view[[
        "WAREHOUSE_NAME", "CURRENT_SIZE", "RECOMMENDED_SIZE", "ACTION", "CONFIDENCE",
        "MIN_CLUSTERS", "MAX_CLUSTERS", "QUERY_COUNT", "P90_MB",
        "REMOTE_SPILL_PCT", "LOCAL_SPILL_PCT", "QUEUE_SECONDS", "TOTAL_CREDITS", "REASONS",
    ]].copy()
    table.columns = [
        "Warehouse", "Current", "Recommended", "Action", "Confidence",
        "Min clusters", "Max clusters", "Queries", "p90 scan (MB)",
        "Remote spill %", "Local spill %", "Queue (s)", "Credits", "Why",
    ]
    st.dataframe(
        table, use_container_width=True, hide_index=True,
        column_config={
            "Why": st.column_config.TextColumn(width="large"),
            "p90 scan (MB)": st.column_config.NumberColumn(format="%.1f"),
            "Remote spill %": st.column_config.NumberColumn(format="%.1f%%"),
            "Local spill %": st.column_config.NumberColumn(format="%.1f%%"),
            "Queue (s)": st.column_config.NumberColumn(format="%.0f"),
            "Credits": st.column_config.NumberColumn(format="%.2f"),
            "Queries": st.column_config.NumberColumn(format="%d"),
        },
    )

# --- Chart: current vs recommended credit rate ---
st.markdown("---")
st.subheader("Current vs recommended capacity")
st.caption(
    "Credits per hour at peak, comparing the current size against the recommendation. This is capacity "
    "cost, not actual spend — a warehouse only bills while it runs."
)
chart_rows = []
for _, r in advice.iterrows():
    cur = normalise_size(r["CURRENT_SIZE"])
    if cur is None:
        continue
    chart_rows.append({"WAREHOUSE": r["WAREHOUSE_NAME"], "STATE": "Current",
                       "CREDITS_PER_HR": SIZE_CREDITS[cur]})
    chart_rows.append({"WAREHOUSE": r["WAREHOUSE_NAME"], "STATE": "Recommended",
                       "CREDITS_PER_HR": SIZE_CREDITS.get(r["RECOMMENDED_SIZE"], SIZE_CREDITS[cur])})

if chart_rows:
    chart_df = pd.DataFrame(chart_rows)
    top_whs = (advice.nlargest(min(15, len(advice)), "TOTAL_CREDITS")["WAREHOUSE_NAME"].tolist())
    chart_df = chart_df[chart_df["WAREHOUSE"].isin(top_whs)]
    cmp_chart = (
        alt.Chart(chart_df)
        .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4, opacity=0.9)
        .encode(
            x=alt.X("WAREHOUSE:N", title="", axis=alt.Axis(labelAngle=-35, labelFontSize=11)),
            y=alt.Y("CREDITS_PER_HR:Q", title="Credits per hour"),
            color=alt.Color("STATE:N", title="",
                            scale=alt.Scale(domain=["Current", "Recommended"],
                                            range=["#8fa6bd", "#29ABE2"])),
            xOffset="STATE:N",
            tooltip=[alt.Tooltip("WAREHOUSE:N", title="Warehouse"),
                     alt.Tooltip("STATE:N", title=""),
                     alt.Tooltip("CREDITS_PER_HR:Q", title="Credits/hr")],
        )
        .properties(height=340).configure_axis(gridOpacity=0.3).configure_view(strokeWidth=0)
    )
    st.altair_chart(cmp_chart, use_container_width=True)

# --- Generated ALTER statements ---
st.markdown("---")
st.subheader("Apply the recommendations")
st.caption(
    "Review every statement before running it. Change one warehouse at a time and re-measure — "
    "sizing is empirical, and this advisor only sees what already happened."
)
changes = advice[advice["ACTION"].isin(["size up", "size down", "add clusters"])]
if len(changes) == 0:
    st.info("No changes recommended — every warehouse looks reasonably sized for its workload.")
else:
    lines = []
    for _, r in changes.iterrows():
        cur = normalise_size(r["CURRENT_SIZE"])
        rec = r["RECOMMENDED_SIZE"]
        wh = r["WAREHOUSE_NAME"]
        parts = []
        if rec != cur and rec in SIZE_SQL:
            parts.append(f"WAREHOUSE_SIZE = '{SIZE_SQL[rec]}'")
        if int(r["MAX_CLUSTERS"]) > 1:
            parts.append(f"MIN_CLUSTER_COUNT = {int(r['MIN_CLUSTERS'])}")
            parts.append(f"MAX_CLUSTER_COUNT = {int(r['MAX_CLUSTERS'])}")
        if not parts:
            continue
        lines.append(f"-- {wh}: {r['ACTION']} ({r['CONFIDENCE']} confidence)")
        lines.append(f'ALTER WAREHOUSE "{wh}" SET')
        lines.append("    " + ",\n    ".join(parts) + ";")
        lines.append("")
    if lines:
        st.code("\n".join(lines), language="sql")
    else:
        st.info("No ALTER statements needed for the selected recommendations.")

st.markdown("---")
st.caption(f"SnowLens Warehouse Sizing Advisor · Questions or feedback? {CONTACT_EMAIL}")
