"""
Audit analytics on real UK government supplier payments (DHSC, spending over £25,000).

Tests (the same kind an audit / AP analytics team runs):
  1. Overview by month
  2. Supplier concentration (Pareto: how many suppliers receive 80% of the money)
  3. Possible duplicate payments: same supplier + same amount within 30 days
  4. Round-amount payments (exact multiples of £10,000) - often estimates or manual entries
  5. Benford's law: first-digit distribution of amounts (a standard fraud-screening test)
  6. Data quality: redacted suppliers, missing fields

Run from the project root, after python/download_gov_spend.py:
    python python/analyze_gov_spend.py
Output: printed summary + CSV files in real_data/results/ (for Power BI)
"""

import math
import os
import sys

import pandas as pd

YEAR = int(sys.argv[1]) if len(sys.argv) > 1 else 2025
SRC = f"real_data/dhsc_spend_{YEAR}.csv"
OUT = "real_data/results"
DUP_WINDOW_DAYS = 30

pd.set_option("display.width", 160)
pd.set_option("display.max_colwidth", 45)
os.makedirs(OUT, exist_ok=True)


def gbp(x):
    return f"£{x:,.0f}"


df = pd.read_csv(SRC, parse_dates=["date"], dtype={"transaction_number": str})
df["supplier_redacted"] = (df["supplier_redacted"].astype(str).str.lower().eq("true")
                           | df["supplier"].astype(str).str.strip().str.lower()
                             .isin(["not available", "n/a", "redacted", "(redacted)", "", "nan"]))
df.loc[df["supplier_redacted"], "supplier"] = "(redacted)"
df["month"] = df["date"].dt.to_period("M").astype(str)

# ------------------------------------------------------------------ segmentation
# The file mixes two very different things:
#   * funding & transfers inside government (budget to NHS England, agencies, devolved
#     governments, capital (PDC) and loans to NHS trusts) - not procurement
#   * third-party spend - payments to companies, universities, charities
# Audit tests are only meaningful on the second group, so we separate them first.
FUNDING_TYPES = (r"grant in aid|parliamentary funding|pdc|loan|dividend|funding \(cash\)|"
                 r"transfer|repay|capital grants? to (?:nhs|public)|ncb")
GOV_BODIES = (r"\bnhs england\b|government|ministry|department|\bhm\b|agency|"
              r"executive|nhs business services|nhs resolution|nhs blood|authority|"
              r"office for|commission|council\b")


et = df["expense_type"].astype(str)
sp = df["supplier"].astype(str)
df["segment"] = "Third-party spend"
df.loc[sp.str.contains(GOV_BODIES, case=False, regex=True), "segment"] = "Government funding & transfers"
df.loc[et.str.contains(FUNDING_TYPES, case=False, regex=True), "segment"] = "Government funding & transfers"
df.loc[df["amount"] <= 0, "segment"] = "Receipts & refunds"

print("=" * 78)
print("0. SEGMENTATION")
print("=" * 78)
seg = df.groupby("segment").agg(payments=("payment_id", "size"), total_gbp=("amount", "sum"))
seg["share_of_value_pct"] = (100 * seg["total_gbp"] / df.loc[df["amount"] > 0, "amount"].sum()).round(1)
print(seg.assign(total_gbp=seg["total_gbp"].map(gbp)).to_string())
seg.reset_index().to_csv(f"{OUT}/segments.csv", index=False)
print("Tests 2-5 below run on THIRD-PARTY SPEND only.\n")

tp = df[df["segment"].eq("Third-party spend")].copy()   # third-party spend
named = tp[~tp["supplier_redacted"]].copy()

# ------------------------------------------------------------------ 1. overview
print("=" * 78)
print(f"1. OVERVIEW  |  {SRC}")
print("=" * 78)
print(f"Payments: {len(df):,}   Suppliers: {df.loc[~df['supplier_redacted'], 'supplier'].nunique():,}   "
      f"Total (net of receipts): {gbp(df['amount'].sum())}")
print(f"Median payment: {gbp(df['amount'].median())}   Largest: {gbp(df['amount'].max())}")
by_month = df.groupby("month").agg(payments=("payment_id", "size"), total_gbp=("amount", "sum")).reset_index()
print(by_month.assign(total_gbp=by_month["total_gbp"].map(gbp)).to_string(index=False))
by_month.to_csv(f"{OUT}/by_month.csv", index=False)

# ------------------------------------------------------------------ 2. concentration
print("\n" + "=" * 78)
print("2. SUPPLIER CONCENTRATION")
print("=" * 78)
sup = (named.groupby("supplier")
       .agg(payments=("payment_id", "size"), total_gbp=("amount", "sum"))
       .sort_values("total_gbp", ascending=False).reset_index())
sup["share_pct"] = 100 * sup["total_gbp"] / sup["total_gbp"].sum()
sup["cumulative_pct"] = sup["share_pct"].cumsum()
sup["rank"] = range(1, len(sup) + 1)
n80 = int((sup["cumulative_pct"] < 80).sum()) + 1
print(f"Third-party spend: {gbp(named['amount'].sum())} to {len(sup)} named suppliers")
print(f"{n80} of {len(sup)} named suppliers ({100 * n80 / len(sup):.1f}%) receive 80% of the money.")
print(f"Top supplier alone: {sup.loc[0, 'supplier']} - {sup.loc[0, 'share_pct']:.1f}%")
print(sup.head(10)[["rank", "supplier", "payments", "total_gbp", "share_pct"]]
      .assign(total_gbp=lambda d: d["total_gbp"].map(gbp), share_pct=lambda d: d["share_pct"].round(1))
      .to_string(index=False))
sup.to_csv(f"{OUT}/supplier_concentration.csv", index=False)

# ------------------------------------------------------------------ 3. possible duplicates
print("\n" + "=" * 78)
print(f"3. POSSIBLE DUPLICATE PAYMENTS (same supplier + same amount within {DUP_WINDOW_DAYS} days)")
print("=" * 78)
d = named.sort_values(["supplier", "amount", "date"]).copy()
d["prev_date"] = d.groupby(["supplier", "amount"])["date"].shift()
d["prev_payment_id"] = d.groupby(["supplier", "amount"])["payment_id"].shift()
d["prev_transaction"] = d.groupby(["supplier", "amount"])["transaction_number"].shift()
d["days_apart"] = (d["date"] - d["prev_date"]).dt.days
dups = d[d["days_apart"].between(0, DUP_WINDOW_DAYS)].copy()
# same transaction number published twice = likely a data/republication issue, not a double payment
dups["same_transaction_number"] = dups["transaction_number"].eq(dups["prev_transaction"])
dups = dups[["payment_id", "prev_payment_id", "supplier", "amount", "date", "prev_date", "days_apart",
             "transaction_number", "prev_transaction", "same_transaction_number", "expense_type", "description"]].copy()
dups["pattern"] = pd.cut(dups["days_apart"], bins=[-1, 0, 26, 31, 999],
                         labels=["Same day", "Within 1-26 days", "Monthly (27-31 days)", "Other"]).astype(str)
dups["priority"] = dups["pattern"].map({"Same day": 1, "Within 1-26 days": 2,
                                        "Monthly (27-31 days)": 3}).fillna(3).astype(int)
dups.loc[dups["same_transaction_number"], "priority"] = 4   # republished row, not a payment
dups = dups.sort_values(["priority", "amount"], ascending=[True, False])
print(f"Pairs flagged: {len(dups):,}   Value of the second payment: {gbp(dups['amount'].sum())}")
print(f"  of which same transaction number (likely re-published rows): {int(dups['same_transaction_number'].sum())}")
print(f"  of which different transaction number (needs review):        {int((~dups['same_transaction_number']).sum())}")
print(dups.groupby("pattern")["amount"].agg(["size", "sum"]).rename(columns={"size": "pairs", "sum": "value_gbp"})
      .assign(value_gbp=lambda x: x["value_gbp"].map(gbp)).to_string())
print("Highest priority first (same day, different transaction number):")
print(dups.head(10)[["supplier", "amount", "date", "days_apart", "pattern", "transaction_number", "prev_transaction"]]
      .assign(amount=lambda x: x["amount"].map(gbp), date=lambda x: x["date"].dt.date,
              )
      .to_string(index=False))
dups.to_csv(f"{OUT}/possible_duplicates.csv", index=False)

# same-day identical transfers inside government funding (not supplier spend, but worth a question)
g = df[df["segment"].eq("Government funding & transfers")]
same_day = g[g.duplicated(["supplier", "amount", "date"], keep=False)].sort_values(["amount", "date"], ascending=False)
print(f"\nGovernment funding: {len(same_day)} payments are identical to another one on the same day "
      f"(same body, same amount), value {gbp(same_day['amount'].sum())}.")
print(same_day.head(6)[["supplier", "amount", "date", "transaction_number", "expense_type"]]
      .assign(amount=lambda x: x["amount"].map(gbp), date=lambda x: x["date"].dt.date).to_string(index=False))
same_day.to_csv(f"{OUT}/funding_same_day_identical.csv", index=False)
print("Note: recurring fixed fees (rent, monthly contracts) also match this rule - each pair needs review.")

# ------------------------------------------------------------------ 4. round amounts
print("\n" + "=" * 78)
print("4. ROUND-AMOUNT PAYMENTS (exact multiples of £10,000)")
print("=" * 78)
tp["is_round"] = (tp["amount"] % 10_000 == 0)
rnd = tp[tp["is_round"]]
print(f"{len(rnd):,} of {len(tp):,} third-party payments ({100 * len(rnd) / len(tp):.1f}%), total {gbp(rnd['amount'].sum())}")
print(rnd.groupby("expense_type")["amount"].agg(["size", "sum"]).sort_values("size", ascending=False).head(8)
      .rename(columns={"size": "payments", "sum": "total_gbp"})
      .assign(total_gbp=lambda x: x["total_gbp"].map(gbp)).to_string())
rnd.to_csv(f"{OUT}/round_amounts.csv", index=False)

# ------------------------------------------------------------------ 5. Benford
print("\n" + "=" * 78)
print("5. BENFORD'S LAW (first digit of the amount)")
print("=" * 78)
first = tp.loc[tp["amount"] > 0, "amount"].map(lambda v: int(str(f"{v:.2f}").lstrip("0.")[0]))
observed = first.value_counts(normalize=True).reindex(range(1, 10), fill_value=0)
expected = pd.Series({k: math.log10(1 + 1 / k) for k in range(1, 10)})
benford = pd.DataFrame({"digit": range(1, 10),
                        "observed_pct": (100 * observed).round(1).values,
                        "expected_pct": (100 * expected).round(1).values})
benford["diff_pp"] = (benford["observed_pct"] - benford["expected_pct"]).round(1)
mad = (observed - expected).abs().mean()
# Nigrini's first-digit MAD thresholds
verdict = ("close conformity" if mad < 0.006 else "acceptable conformity" if mad < 0.012
           else "marginal conformity" if mad < 0.015 else "nonconformity")
print(benford.to_string(index=False))
print(f"MAD = {mad:.4f} -> {verdict} (Nigrini thresholds)")
print("Note: all amounts are above £25,000, which shifts digit 1 and 2 - interpret with care.")
benford.to_csv(f"{OUT}/benford.csv", index=False)

# ------------------------------------------------------------------ 6. data quality
print("\n" + "=" * 78)
print("6. DATA QUALITY")
print("=" * 78)
red = df[df["supplier_redacted"]]
print(f"Redacted supplier (personal data withheld): {len(red):,} payments, {gbp(red['amount'].sum())}")
for col in ["transaction_number", "expense_type", "description"]:
    blank = df[col].isna() | df[col].astype(str).str.strip().isin(["", "Not Available", "nan"])
    print(f"Missing {col:<20} {int(blank.sum()):>6,} rows ({100 * blank.mean():.1f}%)")

df.to_csv(f"{OUT}/payments_clean.csv", index=False)
print(f"\nResults saved to {OUT}/")
