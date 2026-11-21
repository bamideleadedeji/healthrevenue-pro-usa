import numpy as np
import pandas as pd

from config import CARC_DESCRIPTIONS, RCMSummary, get_logger

logger = get_logger("HealthRevenuePro.Analytics")


def analyze_rcm_performance(df: pd.DataFrame) -> tuple[pd.DataFrame, RCMSummary]:
    """
    Computes institutional US healthcare Revenue Cycle Management (RCM) KPIs,
    payer contractual underpayments, A/R aging buckets, and appeal recovery priorities.

    Parameters
    ----------
    df : pd.DataFrame
        Standardized claim-level DataFrame from CMS ingestion or clinic billing upload.

    Returns
    -------
    tuple[pd.DataFrame, RCMSummary]
        Enriched claim-level DataFrame and frozen RCMSummary dataclass instance.

    Raises
    ------
    ValueError
        If input DataFrame is empty or missing required financial columns.
    """
    if df is None or df.empty:
        raise ValueError("Cannot compute RCM analytics on an empty claims dataset.")
    required = {"billed_amount", "expected_allowed", "paid_amount", "days_in_ar"}
    if not required.issubset(set(df.columns)):
        raise ValueError(f"Dataset missing required columns: {required - set(df.columns)}")

    work = df.copy()
    work["contractual_adj"] = (work["billed_amount"] - work["expected_allowed"]).clip(lower=0.0)
    work["underpayment_variance"] = np.where(
        work["claim_status"] != "Denied",
        (work["expected_allowed"] - work["paid_amount"]).clip(lower=0.0),
        0.0,
    )
    work["denied_amount"] = np.where(
        work["claim_status"] == "Denied",
        (work["expected_allowed"] - work["paid_amount"]).clip(lower=0.0),
        0.0,
    )
    work["recoverable_amount"] = (work["underpayment_variance"] + work["denied_amount"]).round(2)

    bins = [-1, 30, 60, 90, 120, 99999]
    labels = ["0-30 Days", "31-60 Days", "61-90 Days", "91-120 Days", "120+ Days"]
    work["ar_bucket"] = pd.cut(work["days_in_ar"], bins=bins, labels=labels).astype(str)

    work["carc_description"] = work["carc_code"].map(CARC_DESCRIPTIONS).fillna("Standard Adjudication / No Denial")
    work["appeal_priority"] = np.where(
        (work["recoverable_amount"] >= 100.0) & (work["days_in_ar"] >= 60),
        "P1 — Immediate Appeal (< Timely Filing)",
        np.where(
            work["recoverable_amount"] > 0.0,
            "P2 — Payer Contract Re-Pricing / Resubmit",
            "Settled",
        ),
    )
    total_claims = int(len(work))
    total_billed = float(work["billed_amount"].sum())
    total_allowed = float(work["expected_allowed"].sum())
    total_paid = float(work["paid_amount"].sum())
    contractual_adj = float(work["contractual_adj"].sum())

    gcr_pct = (total_paid / total_billed * 100.0) if total_billed > 0 else 0.0
    ncr_pct = (total_paid / total_allowed * 100.0) if total_allowed > 0 else 0.0

    underpayment_var = float(work["underpayment_variance"].sum())
    underpaid_count = int((work["underpayment_variance"] > 0.0).sum())
    denied_count = int((work["claim_status"] == "Denied").sum())
    denial_rate_pct = (denied_count / total_claims * 100.0) if total_claims > 0 else 0.0

    avg_dar = float(work["days_in_ar"].mean())
    ar_over_90 = float(work.loc[work["days_in_ar"] > 90, "recoverable_amount"].sum())
    total_recoverable = float(work["recoverable_amount"].sum())
    ar_over_90_pct = (ar_over_90 / total_recoverable * 100.0) if total_recoverable > 0 else 0.0

    summary = RCMSummary(
        total_claims=total_claims,
        total_billed_usd=round(total_billed, 2),
        total_allowed_usd=round(total_allowed, 2),
        total_paid_usd=round(total_paid, 2),
        contractual_adj_usd=round(contractual_adj, 2),
        gross_collection_rate_pct=round(gcr_pct, 2),
        net_collection_rate_pct=round(ncr_pct, 2),
        underpayment_variance_usd=round(underpayment_var, 2),
        underpaid_claim_count=underpaid_count,
        denied_claim_count=denied_count,
        denial_rate_pct=round(denial_rate_pct, 2),
        avg_days_in_ar=round(avg_dar, 1),
        ar_over_90_usd=round(ar_over_90, 2),
        ar_over_90_pct=round(ar_over_90_pct, 1),
        recoverable_opportunity_usd=round(total_recoverable, 2),
    )
    logger.info(
        f"RCM analytics complete | Claims={total_claims} | NCR={summary.net_collection_rate_pct:.2f}% | "
        f"Recoverable=${summary.recoverable_opportunity_usd:,.2f}"
    )
    return work, summary


def build_ar_aging_table(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregates open recoverable balances and claim counts across standard 30-day A/R aging buckets."""
    order = ["0-30 Days", "31-60 Days", "61-90 Days", "91-120 Days", "120+ Days"]
    grp = df.groupby("ar_bucket", observed=False).agg(
        Claim_Count=("claim_id", "count"),
        Billed_USD=("billed_amount", "sum"),
        Expected_Allowed_USD=("expected_allowed", "sum"),
        Paid_USD=("paid_amount", "sum"),
        Recoverable_Balance_USD=("recoverable_amount", "sum"),
    )
    grp = grp.reindex(order, fill_value=0).reset_index().rename(columns={"ar_bucket": "Aging_Bucket"})
    return grp
