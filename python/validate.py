"""
Step 6: fuzzy matching of payments without an invoice number + validation of
results against answer_key.csv.

Run (after generate_data.py):  python run.py validate
Output: output/validation_report.csv, output/fuzzy_matches.csv
Requires: pandas  (optional rapidfuzz: pip install rapidfuzz - faster and better;
without it the script falls back to the built-in difflib)
"""

import os
import re
import sqlite3
import unicodedata
from pathlib import Path

import pandas as pd

try:
    from rapidfuzz import fuzz

    def similarity(a, b):
        return fuzz.token_set_ratio(a, b)
except ImportError:  # fallback without extra libraries
    from difflib import SequenceMatcher

    def similarity(a, b):
        return 100 * SequenceMatcher(None, a, b).ratio()

ROOT = Path(__file__).resolve().parent.parent  # project root, so the script works from any folder
D = ROOT / "data"
OUT = ROOT / "output"
OUT.mkdir(exist_ok=True)
sup = pd.read_csv(f"{D}/suppliers.csv")
po = pd.read_csv(f"{D}/purchase_orders.csv")
grn = pd.read_csv(f"{D}/goods_receipts.csv")
inv = pd.read_csv(f"{D}/supplier_invoices.csv")
pay = pd.read_csv(f"{D}/bank_payments.csv")
fx = pd.read_csv(f"{D}/fx_rates.csv")
key = pd.read_csv(f"{D}/answer_key.csv")

for df, cols in [(inv, ["invoice_date"]), (pay, ["payment_date"]), (fx, ["rate_date"])]:
    for c in cols:
        df[c] = pd.to_datetime(df[c])


# ------------------------------------------------------------------ 1. name normalization
LEGAL_FORMS = {"ltd", "limited", "plc", "inc", "llc", "co", "company", "corp",
               "gmbh", "srl", "bv", "sl", "sro", "ab", "kft", "sa"}


def normalize(name: str) -> str:
    """'NUTS AND DRIED FRUIT COMPANY LTD.' -> 'nuts and dried fruit'; strips legal forms, punctuation, case."""
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().lower()
    s = s.replace("&", " and ").replace(".", "")
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = " ".join(w for w in s.split() if w not in LEGAL_FORMS)
    return re.sub(r"\s+", " ", s).strip()


sup["name_norm"] = sup.supplier_name.map(normalize)


def best_supplier(bank_name):
    n = normalize(bank_name)
    scores = sup.name_norm.map(lambda x: similarity(n, x))
    i = scores.idxmax()
    return sup.supplier_id[i], scores[i]


pay[["supplier_id_guess", "name_score"]] = pay.beneficiary_name.apply(
    lambda n: pd.Series(best_supplier(n)))

# ------------------------------------------------------------------ 2. exact match (same as SQL Q3)
first_inv = inv.sort_values(["invoice_date", "invoice_id"]).drop_duplicates(["supplier_id", "invoice_number"])
exact = pay.merge(first_inv[["invoice_number", "invoice_id"]], left_on="transfer_title",
                  right_on="invoice_number", how="left", indicator=True)
matched_exact = exact[exact._merge == "both"]
unmatched_pay = exact[exact._merge == "left_only"].drop(columns=["invoice_number", "invoice_id", "_merge"])

# ------------------------------------------------------------------ 3. fuzzy: supplier + amount (±2%) + date (75-day window)
fx_map = dict(zip(fx.rate_date, fx.eur_pln))
open_inv = first_inv[~first_inv.invoice_id.isin(matched_exact.invoice_id)].copy()

fuzzy_rows = []
for p in unmatched_pay.itertuples():
    if p.name_score < 70:
        continue
    cand = open_inv[(open_inv.supplier_id == p.supplier_id_guess)
                    & (open_inv.invoice_date <= p.payment_date)
                    & (open_inv.invoice_date >= p.payment_date - pd.Timedelta(days=75))].copy()
    if cand.empty:
        continue
    rate = fx_map.get(p.payment_date)
    if rate is None:  # no EUR rate for that day: only PLN invoices can be compared
        cand = cand[cand.currency.eq("PLN")]
        if cand.empty:
            continue
    cand["expected_pln"] = cand.amount * cand.currency.map({"EUR": rate, "PLN": 1.0})
    cand["amt_diff"] = (cand.expected_pln - p.amount_pln).abs() / cand.expected_pln
    best = cand.sort_values("amt_diff").iloc[0]
    if best.amt_diff <= 0.02:
        fuzzy_rows.append({"payment_id": p.payment_id, "invoice_id": best.invoice_id,
                           "name_score": round(p.name_score), "amt_diff_pct": round(100 * best.amt_diff, 2)})
        open_inv = open_inv[open_inv.invoice_id != best.invoice_id]

fuzzy = pd.DataFrame(fuzzy_rows, columns=["payment_id", "invoice_id", "name_score", "amt_diff_pct"])
still_unmatched = unmatched_pay[~unmatched_pay.payment_id.isin(fuzzy.payment_id)]

print("=== Payment matching ===")
print(f"Total payments:                 {len(pay)}")
print(f"Matched by title (SQL):         {len(matched_exact)}")
print(f"Matched by fuzzy (Python):      {len(fuzzy)}")
print(f"Unmatched:                      {len(still_unmatched)}  "
      f"(total {still_unmatched.amount_pln.sum():,.2f} PLN)")
print(f"Payment match rate:             {100 * (len(matched_exact) + len(fuzzy)) / len(pay):.1f}%\n")

# ------------------------------------------------------------------ 4. SQL results (same rules as sql/03_reconciliation.sql)
con = sqlite3.connect(":memory:")
for name, df in [("suppliers", sup.drop(columns="name_norm")), ("purchase_orders", po),
                 ("goods_receipts", grn), ("supplier_invoices", pd.read_csv(f"{D}/supplier_invoices.csv")),
                 ("bank_payments", pd.read_csv(f"{D}/bank_payments.csv")),
                 ("fx_rates", pd.read_csv(f"{D}/fx_rates.csv"))]:
    df.to_sql(name, con, index=False)
sql_path = ROOT / "sql" / "03_reconciliation.sql"
sql = "\n".join(l.split("--")[0] for l in open(sql_path, encoding="utf-8").read().splitlines())
for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
    if stmt.upper().startswith(("CREATE", "DROP")):
        con.execute(stmt)

tw = pd.read_sql("SELECT * FROM v_three_way", con)
dups = pd.read_sql("SELECT * FROM v_duplicate_invoices", con)
ps = pd.read_sql("SELECT * FROM v_payment_status", con)

# ------------------------------------------------------------------ 5. comparison with the answer key
found = {
    "MISSING_GOODS_RECEIPT": set(tw.loc[tw.match_status == "INVOICE_WITHOUT_RECEIPT", "po_id"]),
    "QTY_INVOICED_ABOVE_RECEIVED": set(tw.loc[tw.match_status == "QTY_INVOICED_ABOVE_RECEIVED", "invoice_id"]),
    "PRICE_ABOVE_PO": set(tw.loc[tw.match_status == "PRICE_ABOVE_PO", "invoice_id"]),
    "INVOICE_WITHOUT_PO": set(tw.loc[tw.match_status == "INVOICE_WITHOUT_PO", "invoice_id"]),
    "LATE_RECEIPT_TIMING": set(tw.loc[tw.match_status == "MATCHED_TIMING", "po_id"]),
    "DUPLICATE_INVOICE": set(dups.invoice_id),
    "FX_STALE_RATE": set(ps.loc[ps.payment_status == "FX_DIFFERENCE", "payment_id"]),
    "PARTIAL_PAYMENT": set(ps.loc[ps.payment_status == "PARTIAL_PAYMENT", "payment_id"]),
    "MISSING_INVOICE_REF": set(fuzzy.payment_id),
    "PAYMENT_WITHOUT_INVOICE": set(still_unmatched.payment_id),
}

rows = []
for err, ids_found in found.items():
    expected = set(key.loc[key.error_type == err, "record_id"])
    # one category per record, so we count the intersection with the expected set
    tp = len(expected & ids_found)
    rows.append({"error_type": err, "planted": len(expected), "found": len(ids_found),
                 "correct": tp,
                 "recall_%": round(100 * tp / len(expected), 1) if expected else None,
                 "precision_%": round(100 * tp / len(ids_found), 1) if ids_found else None})
report = pd.DataFrame(rows)
print("=== Validation against answer_key.csv ===")
print(report.to_string(index=False))
print("\nNot checked directly: SUPPLIER_NAME_VARIANT (handled by name normalization),")
print("UNPAID_INVOICE (needs aging vs payment terms), PARTIAL_RECEIPT_OK (correct by design).")

report.to_csv(OUT / "validation_report.csv", index=False)
fuzzy.to_csv(OUT / "fuzzy_matches.csv", index=False)
print(f"\nSaved {OUT.name}/validation_report.csv and {OUT.name}/fuzzy_matches.csv")
