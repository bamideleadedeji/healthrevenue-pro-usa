import io
from datetime import datetime, timezone
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from cms_ingestion import fetch_live_cms_claims, standardize_clinic_ledger
from config import (
    APP_TITLE,
    APP_VERSION,
    CACHE_TTL_SECONDS,
    DEFAULT_MEDICARE_MULTIPLIER,
    MGMA_TARGET_DAR_DAYS,
    MGMA_TARGET_DENIAL_RATE_PCT,
    MGMA_TARGET_NCR_PCT,
    RCMSummary,
    get_logger,
)
from rcm_analytics_engine import analyze_rcm_performance, build_ar_aging_table

logger = get_logger("HealthRevenuePro.App")

st.set_page_config(
    page_title=APP_TITLE,
    page_icon="🏥",
    layout="wide",
    initial_sidebar_state="expanded",
)


def build_rcm_excel(df: pd.DataFrame, aging_df: pd.DataFrame, summary: RCMSummary, source_msg: str) -> bytes:
    """
    Generates a multi-sheet CFO & Billing Director Revenue Cycle Recovery Workbook (.xlsx).

    Parameters
    ----------
    df : pd.DataFrame
        Enriched claim-level DataFrame with underpayment variances and appeal priorities.
    aging_df : pd.DataFrame
        Aggregated 30-day Accounts Receivable aging table.
    summary : RCMSummary
        Frozen RCMSummary dataclass instance.
    source_msg : str
        Verified data source banner string.

    Returns
    -------
    bytes
        Binary .xlsx workbook payload ready for download.
    """
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        exec_rows = [
            {"Metric": "Audit Generated (UTC)", "Value": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")},
            {"Metric": "Engine Version", "Value": f"v{APP_VERSION}"},
            {"Metric": "Verified Data Source", "Value": source_msg},
            {"Metric": "Total Claims Audited", "Value": summary.total_claims},
            {"Metric": "Gross Billed Charges ($)", "Value": f"${summary.total_billed_usd:,.2f}"},
            {"Metric": "Contractual Expected Allowed ($)", "Value": f"${summary.total_allowed_usd:,.2f}"},
            {"Metric": "Total Net Collections Paid ($)", "Value": f"${summary.total_paid_usd:,.2f}"},
            {"Metric": "Gross Collection Rate (GCR %)", "Value": f"{summary.gross_collection_rate_pct:.2f}%"},
            {"Metric": "Net Collection Rate (NCR % vs 96% MGMA)", "Value": f"{summary.net_collection_rate_pct:.2f}%"},
            {"Metric": "Payer Underpayment Variance ($)", "Value": f"${summary.underpayment_variance_usd:,.2f} ({summary.underpaid_claim_count} claims)"},
            {"Metric": "Claim Denial Rate (% vs 5% MGMA)", "Value": f"{summary.denial_rate_pct:.2f}% ({summary.denied_claim_count} claims)"},
            {"Metric": "Average Days in A/R (vs 35d MGMA)", "Value": f"{summary.avg_days_in_ar:.1f} days"},
            {"Metric": "A/R Over 90 Days Recoverable ($)", "Value": f"${summary.ar_over_90_usd:,.2f} ({summary.ar_over_90_pct:.1f}%)"},
            {"Metric": "Total Recoverable Opportunity ($)", "Value": f"${summary.recoverable_opportunity_usd:,.2f}"},
        ]
        pd.DataFrame(exec_rows).to_excel(writer, sheet_name="01_Executive_RCM_Summary", index=False)
        aging_df.to_excel(writer, sheet_name="02_AR_Aging_Buckets", index=False)

        worklist = df[df["recoverable_amount"] > 0].sort_values(by="recoverable_amount", ascending=False)
        cols = [
            c for c in ["claim_id", "cpt_code", "procedure_desc", "payer", "provider", "billed_amount", "expected_allowed", "paid_amount", "recoverable_amount", "days_in_ar", "claim_status", "carc_code", "appeal_priority"]
            if c in worklist.columns
        ]
        worklist[cols].to_excel(writer, sheet_name="03_Appeal_Recovery_Worklist", index=False)

    logger.info(f"Generated RCM Excel recovery workbook ({len(df)} claims)")
    return buf.getvalue()


def build_cfo_rcm_memo(summary: RCMSummary, source_msg: str) -> str:
    """Generates an executive Revenue Cycle Management memorandum for practice leadership."""
    lines = [
        "======================================================================",
        "HEALTHREVENUEPRO USA — EXECUTIVE REVENUE CYCLE & UNDERPAYMENT REPORT",
        "======================================================================",
        f"Generated (UTC)         : {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"Verified Ledger Source  : {source_msg}",
        "----------------------------------------------------------------------",
        "1. COLLECTION PERFORMANCE VS. MGMA BENCHMARKS",
        f"   - Total Claims Audited        : {summary.total_claims:,}",
        f"   - Gross Billed Charges        : ${summary.total_billed_usd:,.2f}",
        f"   - Expected Contract Allowed   : ${summary.total_allowed_usd:,.2f}",
        f"   - Net Payer Collections       : ${summary.total_paid_usd:,.2f}",
        f"   - Net Collection Rate (NCR)   : {summary.net_collection_rate_pct:.2f}% (MGMA Target: >= {MGMA_TARGET_NCR_PCT:.1f}%)",
        f"   - Average Days in A/R (DAR)   : {summary.avg_days_in_ar:.1f} days (MGMA Target: <= {MGMA_TARGET_DAR_DAYS:.0f} days)",
        "",
        "2. IDENTIFIED REVENUE LEAKAGE & RECOVERY PIPELINE",
        f"   - Contractual Underpayments   : ${summary.underpayment_variance_usd:,.2f} across {summary.underpaid_claim_count:,} claims",
        f"   - Claim Denial Rate           : {summary.denial_rate_pct:.2f}% ({summary.denied_claim_count:,} denied claims)",
        f"   - Aged A/R (> 90 Days)        : ${summary.ar_over_90_usd:,.2f} ({summary.ar_over_90_pct:.1f}% of recoverable balance)",
        f"   - Total Recoverable Revenue   : ${summary.recoverable_opportunity_usd:,.2f}",
        "",
        "3. IMMEDIATE BILLING TEAM ACTION PLAN",
        "   - Submit formal payer underpayment appeals citing contracted Medicare",
        "     fee schedule multiples for all P1/P2 CO-45 underpaid encounters.",
        "   - Prioritize claims in the 61-90 and 91-120 day buckets ahead of",
        "     90-day / 120-day commercial payer timely-filing deadlines.",
        "======================================================================",
    ]
    return "\n".join(lines)


@st.cache_data(ttl=CACHE_TTL_SECONDS)
def cached_cms_claims(limit: int, contract_multiplier: float) -> tuple[pd.DataFrame, str]:
    """Cached wrapper around live US CMS Open Data API (data.cms.gov) ingestion."""
    return fetch_live_cms_claims(limit=limit, contract_multiplier=contract_multiplier)
logger.info(f"Starting {APP_TITLE} v{APP_VERSION}")
st.title(f"🏥 {APP_TITLE}")
st.caption(f"v{APP_VERSION} • 100% Real Data • Live US CMS Medicare Fee Benchmarks (data.cms.gov) • Payer Underpayment & A/R Recovery Engine")

st.sidebar.header("⚙️ RCM Audit Controls")
data_mode = st.sidebar.radio(
    "Select Verified Billing Data Source",
    ["🌐 Live US CMS Medicare API (data.cms.gov)", "📁 Upload Clinic Billing CSV / Excel"],
)

uploaded_file = None
cms_limit = 300
contract_mult = DEFAULT_MEDICARE_MULTIPLIER

if data_mode == "🌐 Live US CMS Medicare API (data.cms.gov)":
    cms_limit = st.sidebar.slider("CMS Medicare CPT Lines to Audit", 50, 1000, 300, 50)
    contract_mult = st.sidebar.slider("Commercial Payer Contract (% of Medicare)", 1.00, 2.00, DEFAULT_MEDICARE_MULTIPLIER, 0.05)
if data_mode == "📁 Upload Clinic Billing CSV / Excel":
    uploaded_file = st.sidebar.file_uploader("Upload Practice 835 / Billing Export", type=["csv", "xlsx", "xls"])

if st.sidebar.button("🔄 Refresh RCM Pipeline"):
    st.cache_data.clear()
    st.rerun()

raw_df = None
source_msg = ""

if data_mode == "🌐 Live US CMS Medicare API (data.cms.gov)":
    try:
        with st.spinner("Streaming live Medicare CPT payment benchmarks from US CMS Open Data API..."):
            raw_df, source_msg = cached_cms_claims(int(cms_limit), float(contract_mult))
    except (ValueError, RuntimeError) as exc:
        st.error(f"CMS Open Data API error: {exc}")
        st.stop()

if data_mode == "📁 Upload Clinic Billing CSV / Excel":
    if uploaded_file is None:
        st.info("👈 Upload your clinic's **CSV or Excel** billing export in the sidebar to run a full underpayment & A/R recovery audit (zero simulated data is ever used).")
        st.stop()
    try:
        df_in = pd.read_csv(uploaded_file) if uploaded_file.name.lower().endswith(".csv") else pd.read_excel(uploaded_file)
        raw_df = standardize_clinic_ledger(df_in)
        source_msg = f"CLINIC BILLING LEDGER: {uploaded_file.name} ({len(raw_df):,} verified claims)"
    except Exception as exc:
        st.error(f"Could not parse billing file: {exc}")
        st.stop()

st.success(f"✅ **Verified Audit Source:** {source_msg}")

scored_df, summary = analyze_rcm_performance(raw_df)
aging_df = build_ar_aging_table(scored_df)

k1, k2, k3, k4 = st.columns(4)
k1.metric("Net Collection Rate (NCR)", f"{summary.net_collection_rate_pct:.1f}%", delta=f"{summary.net_collection_rate_pct - MGMA_TARGET_NCR_PCT:+.1f}% vs MGMA 96%")
k2.metric("Recoverable Revenue Leakage", f"${summary.recoverable_opportunity_usd:,.2f}", delta=f"{summary.underpaid_claim_count + summary.denied_claim_count} actionable claims", delta_color="inverse")
k3.metric("Average Days in A/R (DAR)", f"{summary.avg_days_in_ar:.1f} d", delta=f"{summary.avg_days_in_ar - MGMA_TARGET_DAR_DAYS:+.1f}d vs MGMA 35d", delta_color="inverse")
k4.metric("Claim Denial Rate", f"{summary.denial_rate_pct:.1f}%", delta=f"MGMA Target <= {MGMA_TARGET_DENIAL_RATE_PCT:.0f}%", delta_color="inverse")

st.markdown("---")
t1, t2, t3, t4 = st.tabs([
    "📊 Payer Underpayment Audit",
    "⏳ A/R Aging & 90+ Day Risk",
    "🚫 CARC Denial Analytics",
    "📑 Appeal Worklist & CFO Exports",
])

with t1:
    st.subheader("Contractual Allowed vs. Actual Payer Reimbursement")
    fig_up = px.scatter(
        scored_df,
        x="expected_allowed",
        y="paid_amount",
        color="claim_status",
        hover_data=["claim_id", "cpt_code", "procedure_desc", "recoverable_amount"],
        title="Expected Contract Allowable ($) vs. Actual Payer Paid ($)",
    )
    fig_up.update_layout(height=410)
    st.plotly_chart(fig_up, use_container_width=True)

with t2:
    st.subheader("Accounts Receivable (A/R) Aging Distribution")
    a1, a2 = st.columns(2)
    a1.metric("Aged A/R Over 90 Days ($)", f"${summary.ar_over_90_usd:,.2f}")
    a2.metric("Share of Recoverable Balance (>90d)", f"{summary.ar_over_90_pct:.1f}%")

    fig_ar = px.bar(aging_df, x="Aging_Bucket", y="Recoverable_Balance_USD", text_auto=".2s", title="Recoverable A/R Balance by Aging Bucket ($)")
    fig_ar.update_layout(height=360)
    st.plotly_chart(fig_ar, use_container_width=True)
    st.dataframe(aging_df, use_container_width=True)

with t3:
    st.subheader("CARC Adjustment & Denial Root-Cause Breakdown")
    actionable = scored_df[scored_df["carc_code"] != "NONE"]
    if not actionable.empty:
        carc_grp = actionable.groupby(["carc_code", "carc_description"], as_index=False).agg(
            Affected_Claims=("claim_id", "count"),
            Recoverable_USD=("recoverable_amount", "sum"),
        ).sort_values("Recoverable_USD", ascending=False)
        fig_carc = px.bar(carc_grp, x="carc_code", y="Recoverable_USD", color="carc_code", title="Recoverable Revenue by CARC Reason Code ($)")
        fig_carc.update_layout(height=350)
        st.plotly_chart(fig_carc, use_container_width=True)
        st.dataframe(carc_grp, use_container_width=True)
    if actionable.empty:
        st.info("No CARC denials or underpayments detected in the selected cohort.")

with t4:
    st.subheader("Prioritized Payer Appeal Worklist & Executive Deliverables")
    worklist = scored_df[scored_df["recoverable_amount"] > 0].sort_values(by="recoverable_amount", ascending=False)
    show_cols = [
        c for c in ["claim_id", "cpt_code", "procedure_desc", "billed_amount", "expected_allowed", "paid_amount", "recoverable_amount", "days_in_ar", "carc_code", "appeal_priority"]
        if c in worklist.columns
    ]
    st.dataframe(worklist[show_cols], use_container_width=True)

    e1, e2, e3 = st.columns(3)
    with e1:
        xb = build_rcm_excel(scored_df, aging_df, summary, source_msg)
        st.download_button("📊 Download CFO Excel Recovery Workbook (.xlsx)", xb, "HealthRevenuePro_RCM_Recovery_Audit.xlsx")
    with e2:
        mt = build_cfo_rcm_memo(summary, source_msg)
        st.download_button("📝 Download Executive RCM Memo (.txt)", mt.encode("utf-8"), "HealthRevenuePro_Executive_Memo.txt")
    with e3:
        cd = worklist[show_cols].to_csv(index=False).encode("utf-8")
        st.download_button("📥 Download Appeal Worklist (.csv)", cd, "HealthRevenuePro_Appeal_Worklist.csv")
