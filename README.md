# Procure-to-Pay Reconciliation

**SQL (PostgreSQL, SQLite) · Python (pandas) · Power BI · DAX**

An Accounts Payable audit project in two parts:

- **Part A:** a 3-way match (PO ↔ goods receipt ↔ invoice) plus bank reconciliation on synthetic data, validated against an answer key of planted errors.
- **Part B:** audit analytics on **18,819 real UK Department of Health payments (£214.7bn, 2025)**.

![Executive Overview dashboard](images/executive_overview.png)

---

## Part A – Reconciliation

**Problem:** payments to suppliers exceeded the value of goods received. The task was to match every payment to its invoice, goods receipt and purchase order, then quantify the losses.

| Metric | Result |
| --- | --- |
| POs passing 3-way match | **91.6%** (2,790 of 3,045) |
| Value at risk | **3.79M PLN** (3.2% of invoiced) |
| Duplicate invoices paid twice | **30 invoices, 1.08M PLN** |
| Overpayment from stale EUR rate | **0.26M PLN** on 40 payments |
| Payments matched automatically | **99.5%** (SQL exact + Python fuzzy) |

**Key findings**
- Invoices with no goods receipt make up **63% of the value at risk** (2.37M PLN).
- **3 suppliers cause 35% of discrepancies**, so targeted supplier reviews pay off fastest.
- 170 orders only *look* wrong: 100 have a late goods receipt and 70 are correctly invoiced partial deliveries. Telling these apart from real losses avoids wasted review time.

**Validation:** 10 planted error types were all detected with **100% recall and 100% precision**, measured against the answer key (`output/validation_report.csv`).

## Part B – Real data audit (UK DHSC, 2025)

Source: [DHSC spending over £25,000](https://www.gov.uk/government/collections/dhsc-spending-over-25000) (Open Government Licence v3.0).

![Real data audit dashboard](images/real_data_audit.png)

- **Understand the data before testing it.** 98% of the value is government funding (for example £199bn to NHS England), not purchasing. The audit tests run only on the **£3.9bn of third-party spend**.
- **Duplicates:** 581 candidate pairs (same supplier and amount within 30 days) were narrowed to **65 same-day pairs (£3.7M)** that need review. Most of the others follow a monthly contract pattern.
- **Top item to review:** Fresenius Medical Care received 4 × £201,110 on the same day, each with a different transaction number.
- **Benford's law** shows nonconformity, but this is explained by the £25k publication threshold rather than manipulation.

## Skills demonstrated

| Area | Techniques |
| --- | --- |
| SQL | Star schema, PK/FK constraints, views, CTEs, window functions (`ROW_NUMBER`, `RANK`), anti-joins, a portable full outer join |
| Python | pandas, data generation with planted errors, fuzzy matching (rapidfuzz), messy CSV ingestion from GOV.UK |
| Analytics | 3-way match, duplicate detection, FX reconciliation, Pareto, Benford, validation with precision/recall |
| Power BI | Star schema, DAX measures, Top N, slicers |

## How to run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python run.py demo        # Part A: generate data → validate → SQL (SQLite)
python run.py analyze     # Part B: audit tests on the 2025 data
```

The optional PostgreSQL workflow runs the scripts `sql/01_schema.sql` → `04_export_powerbi.sql` in order, from the project root. After that, open `powerbi/p2p_reconciliation.pbix`.

## Structure

```
python/     data generation, validation, SQL runner, GOV.UK download + analysis
sql/        01_schema → 02_load → 03_reconciliation → 04_export_powerbi
real_data/  2025 DHSC payments + audit results
powerbi/    dashboard + its data
run.py      single entry point for all workflows
```

---

**Maria Kurzeja** · [GitHub](https://github.com/MarKurzeja)
