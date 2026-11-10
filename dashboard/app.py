"""
YottaFlex Cart Recovery — Streamlit Dashboard
==============================================
6 tabs:
  1. Overview   — 8 KPI metric cards
  2. Funnel     — Plotly conversion funnel chart
  3. Campaigns  — per-campaign performance table + bar chart
  4. Consent    — opt-in/out pie charts per channel
  5. Edge Cases — ground-truth anomaly table
  6. Repro      — reproducibility hash-comparison table

Requirements: streamlit, plotly, sqlalchemy, psycopg[binary]
Run: streamlit run dashboard/app.py
"""

from __future__ import annotations

import os
import json
from pathlib import Path

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from sqlalchemy import create_engine, text

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://yottaflex:yottaflex_local_only@localhost:5433/yottaflex",
)
OUTPUT_DIR = Path("output")

st.set_page_config(
    page_title="YottaFlex Cart Recovery Analytics",
    page_icon="🛒",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ---------------------------------------------------------------------------
# DB helper
# ---------------------------------------------------------------------------
@st.cache_resource
def get_engine():
    return create_engine(DATABASE_URL, pool_pre_ping=True, future=True)


def q_scalar(conn, sql: str):
    return conn.execute(text(sql)).scalar_one()


def q_df(conn, sql: str) -> pd.DataFrame:
    rows = conn.execute(text(sql)).fetchall()
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([dict(r._mapping) for r in rows])


# ---------------------------------------------------------------------------
# Title
# ---------------------------------------------------------------------------
st.title("🛒 YottaFlex Cart Recovery Analytics")
st.caption(
    "Synthetic demonstration data only — fixed seed 20261022. "
    "All revenue figures are generated outcomes, not real customer results."
)

# ---------------------------------------------------------------------------
# Connection check
# ---------------------------------------------------------------------------
try:
    _eng = get_engine()
    with _eng.connect() as _c:
        _c.execute(text("SELECT 1"))
    db_ok = True
except Exception as db_err:
    db_ok = False
    st.error(f"⚠️ Database unavailable: {db_err}")
    st.info(
        "Start PostgreSQL first:\n"
        "```powershell\n"
        "docker compose up -d\n"
        "python -m app.generate --small\n"
        "```"
    )
    st.stop()

# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "📊 Overview",
    "🔽 Funnel",
    "📧 Campaigns",
    "✅ Consent",
    "⚠️ Edge Cases",
    "🔁 Reproducibility",
])

# ============================================================
# TAB 1 — Overview
# ============================================================
with tab1:
    st.subheader("Key Performance Indicators")

    with get_engine().connect() as conn:
        kpis = {
            "customers":        q_scalar(conn, "SELECT COUNT(*) FROM customers"),
            "carts":            q_scalar(conn, "SELECT COUNT(*) FROM carts"),
            "abandoned":        q_scalar(conn, "SELECT COUNT(*) FROM carts WHERE abandonment_label = TRUE"),
            "recovered":        q_scalar(conn, "SELECT COUNT(*) FROM carts WHERE status = 'RECOVERED'"),
            "sends":            q_scalar(conn, "SELECT COUNT(*) FROM send_attempts"),
            "delivered":        q_scalar(conn, "SELECT COUNT(*) FROM send_attempts WHERE status = 'DELIVERED'"),
            "suppressed":       q_scalar(conn, "SELECT COUNT(*) FROM send_attempts WHERE status = 'SUPPRESSED'"),
            "freq_capped":      q_scalar(conn, "SELECT COUNT(*) FROM send_attempts WHERE status = 'FREQ_CAP'"),
            "orders":           q_scalar(conn, "SELECT COUNT(*) FROM orders"),
            "recovery_orders":  q_scalar(conn, "SELECT COUNT(*) FROM orders WHERE source = 'RECOVERY'"),
            "attributions":     q_scalar(conn, "SELECT COUNT(*) FROM attributions WHERE issue_type IS NULL"),
            "attributed_rev":   q_scalar(conn, "SELECT COALESCE(SUM(attributed_revenue),0) FROM attributions WHERE issue_type IS NULL"),
            "edge_cases":       q_scalar(conn, "SELECT COUNT(*) FROM edge_cases"),
        }

    r1 = st.columns(4)
    r1[0].metric("Customers",        f"{kpis['customers']:,}")
    r1[1].metric("Total Carts",      f"{kpis['carts']:,}")
    r1[2].metric("Abandoned Carts",  f"{kpis['abandoned']:,}",
                 delta=f"{kpis['abandoned']/kpis['carts']*100:.1f}% of all carts" if kpis["carts"] else "")
    r1[3].metric("Recovered Carts",  f"{kpis['recovered']:,}",
                 delta=f"{kpis['recovered']/kpis['abandoned']*100:.1f}% of abandoned" if kpis["abandoned"] else "")

    r2 = st.columns(4)
    r2[0].metric("Send Attempts",    f"{kpis['sends']:,}")
    r2[1].metric("Delivered",        f"{kpis['delivered']:,}",
                 delta=f"{kpis['delivered']/kpis['sends']*100:.1f}% delivery rate" if kpis["sends"] else "")
    r2[2].metric("Suppressed",       f"{kpis['suppressed']:,}")
    r2[3].metric("Freq-Capped",      f"{kpis['freq_capped']:,}")

    r3 = st.columns(4)
    r3[0].metric("Orders (total)",   f"{kpis['orders']:,}")
    r3[1].metric("Recovery Orders",  f"{kpis['recovery_orders']:,}")
    r3[2].metric("Attributions",     f"{kpis['attributions']:,}")
    r3[3].metric("Attributed Rev",   f"${float(kpis['attributed_rev']):,.2f}")

    st.markdown("---")
    st.metric("Labelled Edge Cases", kpis["edge_cases"])

    # Message outcome bar chart
    st.subheader("Message Outcomes")
    outcome_df = pd.DataFrame({
        "Status": ["Delivered", "Suppressed", "Freq-Capped"],
        "Count":  [kpis["delivered"], kpis["suppressed"], kpis["freq_capped"]],
    })
    fig_out = px.bar(outcome_df, x="Status", y="Count",
                     color="Status",
                     color_discrete_map={"Delivered": "#2ecc71", "Suppressed": "#e74c3c", "Freq-Capped": "#f39c12"},
                     title="Send Attempt Outcomes")
    fig_out.update_layout(showlegend=False)
    st.plotly_chart(fig_out, use_container_width=True)

# ============================================================
# TAB 2 — Funnel
# ============================================================
with tab2:
    st.subheader("Conversion Funnel")

    with get_engine().connect() as conn:
        total_carts = q_scalar(conn, "SELECT COUNT(*) FROM carts")
        abandoned   = q_scalar(conn, "SELECT COUNT(*) FROM carts WHERE abandonment_label = TRUE")
        sent        = q_scalar(conn, "SELECT COUNT(*) FROM send_attempts")
        delivered   = q_scalar(conn, "SELECT COUNT(*) FROM send_attempts WHERE status = 'DELIVERED'")
        opened      = q_scalar(conn, "SELECT COUNT(*) FROM send_attempts WHERE opened_at IS NOT NULL")
        clicked     = q_scalar(conn, "SELECT COUNT(*) FROM send_attempts WHERE clicked_at IS NOT NULL")
        converted   = q_scalar(conn, "SELECT COUNT(*) FROM orders WHERE source = 'RECOVERY'")

    funnel_labels  = ["Total Carts","Abandoned","Sent","Delivered","Opened","Clicked","Converted"]
    funnel_values  = [total_carts, abandoned, sent, delivered, opened, clicked, converted]

    fig_funnel = go.Figure(go.Funnel(
        y      = funnel_labels,
        x      = funnel_values,
        textposition = "inside",
        textinfo     = "value+percent initial",
        marker = dict(color=[
            "#3498db","#e67e22","#9b59b6","#2ecc71","#1abc9c","#f39c12","#e74c3c"
        ]),
    ))
    fig_funnel.update_layout(title="Cart Recovery Conversion Funnel", height=500)
    st.plotly_chart(fig_funnel, use_container_width=True)

    # Stage rates
    def pct(a, b):
        return f"{a/b*100:.1f}%" if b else "—"

    st.markdown("**Stage Conversion Rates**")
    stage_df = pd.DataFrame([
        {"Stage": "Carts → Abandoned",    "Rate": pct(abandoned, total_carts)},
        {"Stage": "Abandoned → Sent",     "Rate": pct(sent, abandoned)},
        {"Stage": "Sent → Delivered",     "Rate": pct(delivered, sent)},
        {"Stage": "Delivered → Opened",   "Rate": pct(opened, delivered)},
        {"Stage": "Opened → Clicked",     "Rate": pct(clicked, opened)},
        {"Stage": "Clicked → Converted",  "Rate": pct(converted, clicked)},
        {"Stage": "Abandoned → Converted","Rate": pct(converted, abandoned)},
    ])
    st.dataframe(stage_df, use_container_width=True, hide_index=True)

# ============================================================
# TAB 3 — Campaigns
# ============================================================
with tab3:
    st.subheader("Campaign Performance")

    with get_engine().connect() as conn:
        camp_df = q_df(conn, """
            SELECT
                c.campaign_id,
                c.name,
                c.channel,
                c.delay_minutes,
                COUNT(s.send_id) AS sent,
                SUM(CASE WHEN s.status='DELIVERED'       THEN 1 ELSE 0 END) AS delivered,
                SUM(CASE WHEN s.opened_at IS NOT NULL    THEN 1 ELSE 0 END) AS opened,
                SUM(CASE WHEN s.clicked_at IS NOT NULL   THEN 1 ELSE 0 END) AS clicked,
                COUNT(DISTINCT o.order_id)                                   AS converted,
                COALESCE(SUM(CASE WHEN a.issue_type IS NULL
                             THEN CAST(a.attributed_revenue AS FLOAT) ELSE 0 END), 0) AS revenue
            FROM campaigns c
            LEFT JOIN send_attempts s ON s.campaign_id = c.campaign_id
            LEFT JOIN attributions  a ON a.campaign_id = c.campaign_id AND a.issue_type IS NULL
            LEFT JOIN orders        o ON o.order_id = a.order_id
            GROUP BY c.campaign_id, c.name, c.channel, c.delay_minutes
            ORDER BY c.campaign_id
        """)

    if not camp_df.empty:
        # Compute rates
        camp_df["delivery_rate"]   = (camp_df["delivered"] / camp_df["sent"].replace(0,1) * 100).round(1)
        camp_df["open_rate"]       = (camp_df["opened"]    / camp_df["delivered"].replace(0,1) * 100).round(1)
        camp_df["click_rate"]      = (camp_df["clicked"]   / camp_df["opened"].replace(0,1) * 100).round(1)
        camp_df["conversion_rate"] = (camp_df["converted"] / camp_df["clicked"].replace(0,1) * 100).round(1)
        camp_df["revenue"]         = camp_df["revenue"].apply(lambda x: f"${float(x):,.2f}")

        display_cols = ["campaign_id","name","channel","delay_minutes",
                        "sent","delivered","delivery_rate",
                        "opened","open_rate","clicked","click_rate",
                        "converted","conversion_rate","revenue"]
        st.dataframe(camp_df[display_cols], use_container_width=True, hide_index=True)

        # Grouped bar chart
        fig_camp = go.Figure()
        metrics  = ["delivered","opened","clicked","converted"]
        colors   = ["#2ecc71","#3498db","#9b59b6","#e74c3c"]
        for metric, color in zip(metrics, colors):
            fig_camp.add_trace(go.Bar(
                name=metric.capitalize(),
                x   =camp_df["name"],
                y   =camp_df[metric],
                marker_color=color,
            ))
        fig_camp.update_layout(
            barmode="group",
            title="Campaign Funnel by Stage",
            xaxis_title="Campaign",
            yaxis_title="Count",
            height=400,
        )
        st.plotly_chart(fig_camp, use_container_width=True)
    else:
        st.info("No campaign data available yet. Run the generator first.")

# ============================================================
# TAB 4 — Consent
# ============================================================
with tab4:
    st.subheader("Consent Breakdown by Channel")

    with get_engine().connect() as conn:
        consent_df = q_df(conn, """
            SELECT channel,
                   SUM(CASE WHEN is_consented THEN 1 ELSE 0 END)     AS opted_in,
                   SUM(CASE WHEN NOT is_consented THEN 1 ELSE 0 END)  AS opted_out
            FROM consents
            GROUP BY channel
        """)
        withdrawals_df = q_df(conn, """
            SELECT channel, COUNT(*) AS withdrawals
            FROM consent_events WHERE event_type = 'OPT_OUT'
            GROUP BY channel
        """)
        edge_withdrawals = q_scalar(conn,
            "SELECT COUNT(*) FROM consent_events WHERE source = 'EC_WITHDRAWAL'")

    if not consent_df.empty:
        col1, col2 = st.columns(2)
        for i, (_, row) in enumerate(consent_df.iterrows()):
            target = col1 if i == 0 else col2
            with target:
                channel = row["channel"]
                fig_pie = go.Figure(go.Pie(
                    labels=["Opted In","Opted Out"],
                    values=[row["opted_in"], row["opted_out"]],
                    marker_colors=["#2ecc71","#e74c3c"],
                    hole=0.4,
                ))
                fig_pie.update_layout(title=f"{channel} Consent", height=300)
                st.plotly_chart(fig_pie, use_container_width=True)

    st.markdown("---")
    st.subheader("Consent Events (Opt-Out Log)")
    if not withdrawals_df.empty:
        st.dataframe(withdrawals_df, use_container_width=True, hide_index=True)
    st.metric("Edge-case opt-out injections (MSG_AFTER_OPT_OUT)", int(edge_withdrawals))

    # Device type breakdown
    with get_engine().connect() as conn:
        device_df = q_df(conn, """
            SELECT device_type, COUNT(*) AS sessions
            FROM sessions GROUP BY device_type ORDER BY sessions DESC
        """)
    if not device_df.empty:
        st.markdown("---")
        st.subheader("Session Device Types")
        fig_dev = px.pie(device_df, values="sessions", names="device_type",
                         title="Sessions by Device",
                         color_discrete_sequence=["#3498db","#2ecc71","#9b59b6"])
        st.plotly_chart(fig_dev, use_container_width=True)

# ============================================================
# TAB 5 — Edge Cases
# ============================================================
with tab5:
    st.subheader("Ground-Truth Labelled Anomalies")
    st.info(
        "These records were **intentionally injected** for evaluation purposes. "
        "They are stored in the `edge_cases` table and exported to `output/ground_truth.csv`. "
        "They are **never merged into input features**."
    )

    with get_engine().connect() as conn:
        ec_df = q_df(conn, "SELECT * FROM edge_cases ORDER BY issue_type, edge_case_id")

    if not ec_df.empty:
        issue_counts = ec_df["issue_type"].value_counts().reset_index()
        issue_counts.columns = ["Issue Type","Count"]

        col1, col2 = st.columns([1, 2])
        with col1:
            st.dataframe(issue_counts, use_container_width=True, hide_index=True)
            fig_ec = px.bar(issue_counts, x="Issue Type", y="Count",
                            color="Issue Type", title="Edge Cases by Type")
            fig_ec.update_layout(showlegend=False)
            st.plotly_chart(fig_ec, use_container_width=True)

        with col2:
            filter_type = st.selectbox("Filter by issue type",
                                       ["All"] + sorted(ec_df["issue_type"].unique().tolist()))
            filtered = ec_df if filter_type == "All" else ec_df[ec_df["issue_type"] == filter_type]
            st.dataframe(filtered[["edge_case_id","issue_type","record_table",
                                   "record_id","expected_decision","description"]],
                         use_container_width=True, hide_index=True, height=400)
    else:
        st.warning("No edge cases found. Run the generator first.")

# ============================================================
# TAB 6 — Reproducibility
# ============================================================
with tab6:
    st.subheader("Reproducibility Verification")
    st.markdown(
        "The generator uses a **fixed seed** (`20261022`). "
        "Running it twice with the same seed must produce **identical row counts and file checksums**."
    )

    repro_path = OUTPUT_DIR / "reproducibility_report.json"

    if repro_path.exists():
        try:
            report = json.loads(repro_path.read_text())
            status_color = "🟢" if report["status"] == "PASS" else "🔴"
            st.metric("Reproducibility Status",
                      f"{status_color} {report['status']}",
                      delta=f"checked at {report.get('checked_at','')[:19]}")
            st.metric("Files Checked",    report.get("matches",0) + report.get("mismatches",0))
            st.metric("Matching Files",   report.get("matches",0))
            st.metric("Mismatching Files",report.get("mismatches",0))

            details = report.get("details", [])
            if details:
                df_rep = pd.DataFrame(details)
                df_rep["match"] = df_rep["match"].apply(lambda x: "✅" if x else "❌")
                df_rep["run1_sha256"] = df_rep["run1_sha256"].str[:20] + "…"
                df_rep["run2_sha256"] = df_rep["run2_sha256"].str[:20] + "…"
                st.dataframe(df_rep, use_container_width=True, hide_index=True)
        except Exception as e:
            st.error(f"Could not parse reproducibility report: {e}")
    else:
        st.warning(
            "No reproducibility report found yet.\n\n"
            "Generate it by running:\n"
            "```powershell\npython -m app.repro --small\n```"
        )

    st.markdown("---")
    st.subheader("Source Manifest")
    manifest_path = OUTPUT_DIR / "source_manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text())
            st.json(manifest)
        except Exception:
            st.warning("Could not load source_manifest.json")
    else:
        st.info("Run `python -m app.export` to generate the source manifest.")
