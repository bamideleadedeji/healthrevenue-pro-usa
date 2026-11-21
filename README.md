# HealthRevenuePro™ USA — Enterprise RCM & Payer Underpayment Engine (v2.1)

100% real-data US healthcare Revenue Cycle Management (RCM) & payer underpayment intelligence suite built in Python and Streamlit. Connects directly to the **US Centers for Medicare & Medicaid Services Open Data API (`data.cms.gov`)** and custom clinic billing exports (Athenahealth, Tebra/Kareo, eClinicalWorks, AdvancedMD, Epic) with zero synthetic or simulated data.

## Architecture

- `config.py` — MGMA benchmarks, CARC denial definitions, `RCMSummary` frozen dataclass, and structured logger
- `cms_ingestion.py` — Resilient US CMS Medicare API pipeline (exponential backoff + HTTP 429 handling) and clinic CSV/Excel auto-mapper
- `rcm_analytics_engine.py` — Net Collection Rate (NCR), Days in A/R (DAR), contractual underpayment variance, 30-day A/R aging buckets, and appeal worklist prioritization
- `app.py` — Executive Streamlit RCM dashboard with multi-sheet Excel (`.xlsx`), CFO Memo (`.txt`), and Appeal Worklist (`.csv`) exports

## Quickstart

```bash
pip install -r requirements.txt
streamlit run app.py
```
