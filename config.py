import logging
from dataclasses import dataclass

APP_TITLE = "HealthRevenuePro™ USA — Enterprise RCM & Payer Underpayment Engine"
APP_VERSION = "2.1.0"

CMS_HOST = ".".join(["data", "cms", "gov"])
CMS_DATASET_ID = "92396110-2aed-4d63-a6a2-5d6207d46a29"
MAX_RETRIES = 3
BACKOFF_BASE_SECONDS = 1.5
REQUEST_TIMEOUT_SECONDS = 20
CACHE_TTL_SECONDS = 1800

MGMA_TARGET_NCR_PCT = 96.0
MGMA_TARGET_DAR_DAYS = 35.0
MGMA_TARGET_DENIAL_RATE_PCT = 5.0
DEFAULT_MEDICARE_MULTIPLIER = 1.25

CARC_DESCRIPTIONS = {
    "CO-4": "Modifier inconsistent with procedure code (Coding/Modifier Scrubbing)",
    "CO-16": "Claim lacks information or has submission/billing error",
    "CO-45": "Charge exceeds fee schedule / maximum allowable contracted amount",
    "CO-50": "Non-covered service — Medical necessity documentation required",
    "CO-97": "Benefit included in payment/allowance for another bundled service",
    "PR-1": "Deductible amount responsibility (Patient statement / secondary billing)",
    "OA-23": "Impact of prior payer adjudication / Coordination of Benefits",
}


@dataclass(frozen=True)
class RCMSummary:
    """Strongly typed output container for US healthcare Revenue Cycle Management metrics."""
    total_claims: int
    total_billed_usd: float
    total_allowed_usd: float
    total_paid_usd: float
    contractual_adj_usd: float
    gross_collection_rate_pct: float
    net_collection_rate_pct: float
    underpayment_variance_usd: float
    underpaid_claim_count: int
    denied_claim_count: int
    denial_rate_pct: float
    avg_days_in_ar: float
    ar_over_90_usd: float
    ar_over_90_pct: float
    recoverable_opportunity_usd: float


def get_logger(name: str = "HealthRevenuePro") -> logging.Logger:
    """Returns a configured structured console logger without duplicate handlers."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        handler = logging.StreamHandler()
        formatter = logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return logger
