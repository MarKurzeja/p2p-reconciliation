"""
Fuzzy matching of payments without an invoice number + validation of the
reconciliation results against answer_key.csv.

Run (after generate_data.py):  python run.py validate [--strict]
Output: output/validation_report.csv, output/fuzzy_matches.csv
--strict exits with an error if any check is below 100% recall or precision (used in CI).

Uses rapidfuzz when installed; otherwise falls back to the built-in difflib.
"""

import re
import sqlite3
import sys
import unicodedata
from pathlib import Path

import pandas as pd

from run_sql import split_statements

try:
    from rapidfuzz import fuzz

    def similarity(a, b):
        return fuzz.token_set_ratio(a, b)
except ImportError:  # fallback without extra libraries
    from difflib import SequenceMatcher

    def similarity(a, b):
        return 100 * SequenceMatcher(None, a, b).ratio()

ROOT = Path(__file__).resolve().parent.parent  # project root, so the script works from any folder
DATA = ROOT / "data"
OUT = ROOT / "output"
SQL_FILE = ROOT / "sql" / "03_reconciliation.sql"
TABLES = ["suppliers", "purchase_orders", "goods_receipts", "supplier_invoices", "bank_payments", "fx_rates"]

NAME_SCORE_MIN = 70        # minimum supplier-name similarity (0-100)
AMOUNT_TOLERANCE = 0.02    # ±2% between payment and invoice amount in PLN
DATE_WINDOW_DAYS = 75      # payment within 75 days after the invoice

LEGAL_FORMS = {"ltd", "limited", "plc", "inc", "llc", "co", "company", "corp",
               "gmbh", "srl", "bv", "sl", "sro", "ab", "kft", "sa"}


def load_data():
    raw = {t: pd.read_csv(DATA / f"{t}.csv") for t in TABLES + ["answer_key"]}
    parsed = {t: df.copy() for t, df in raw.items()}
    for t, col in [("supplier_invoices", "invoice_date"), ("bank_payments", "payment_date"), ("fx_rates", "rate_date")]:
        parsed[t][col] = pd.to_datetime(parsed[t][col])
    return raw, parsed


# ------------------------------------------------------------------ name normalization
def normalize(name: str) -> str:
    """'NUTS AND DRIED FRUIT COMPANY LTD.' -> 'nuts and dried fruit'; strips legal forms, punctuation, case."""
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().lower()
    s = s.replace("&", " and ").replace(".", "")
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = " ".join(w for w in s.split() if w not in LEGAL_FORMS)
    return re.sub(r"\s+", " ", s).strip()


def guess_suppliers(pay, sup):
    """Add the best-matching supplier and its similarity score to each payment."""
    names = sup.supplier_name.map(normalize)

    def best(bank_name):
        scores = names.map(lambda x: similarity(normalize(bank_name), x))
        i = scores.idxmax()
        return sup.supplier_id[i], scores[i]

    pay = pay.copy()
    pay[["supplier_id_guess", "name_score"]] = pay.beneficiary_name.apply(lambda n: pd.Series(best(n)))
    return pay


# ------------------------------------------------------------------ payment matching
def match_payments(pay, inv, fx):
    """Stage 1: exact match on transfer title (as in SQL). Stage 2: fuzzy match the rest."""
    first_inv = inv.sort_values(["invoice_date", "invoice_id"]).drop_duplicates(["supplier_id", "invoice_number"])
    exact = pay.merge(first_inv[["invoice_number", "invoice_id"]], left_on="transfer_title",
                      right_on="invoice_number", how="left", indicator=True)
    matched_exact = exact[exact._merge == "both"]
    unmatched = exact[exact._merge == "left_only"].drop(columns=["invoice_number", "invoice_id", "_merge"])

    fx_map = dict(zip(fx.rate_date, fx.eur_pln))
    open_inv = first_inv[~first_inv.invoice_id.isin(matched_exact.invoice_id)].copy()
    fuzzy_rows = []
    for p in unmatched.itertuples():
        if p.name_score < NAME_SCORE_MIN:
            continue
        cand = open_inv[(open_inv.supplier_id == p.supplier_id_guess)
                        & (open_inv.invoice_date <= p.payment_date)
                        & (open_inv.invoice_date >= p.payment_date - pd.Timedelta(days=DATE_WINDOW_DAYS))].copy()
        rate = fx_map.get(p.payment_date)
        if rate is None:  # no EUR rate for that day: only PLN invoices can be compared
            cand = cand[cand.currency.eq("PLN")]
        if cand.empty:
            continue
        cand["expected_pln"] = cand.amount * cand.currency.map({"EUR": rate, "PLN": 1.0})
        cand["amt_diff"] = (cand.expected_pln - p.amount_pln).abs() / cand.expected_pln
        best = cand.sort_values("amt_diff").iloc[0]
        if best.amt_diff <= AMOUNT_TOLERANCE:
            fuzzy_rows.append({"payment_id": p.payment_id, "invoice_id": best.invoice_id,
                               "name_score": round(p.name_score), "amt_diff_pct": round(100 * best.amt_diff, 2)})
            open_inv = open_inv[open_inv.invoice_id != best.invoice_id]

    fuzzy = pd.DataFrame(fuzzy_rows, columns=["payment_id", "invoice_id", "name_score", "amt_diff_pct"])
    still_unmatched = unmatched[~unmatched.payment_id.isin(fuzzy.payment_id)]
    return matched_exact, fuzzy, still_unmatched


def print_matching_summary(pay, matched_exact, fuzzy, still_unmatched):
    print("=== Payment matching ===")
    print(f"Total payments:                 {len(pay)}")
    print(f"Matched by title (SQL):         {len(matched_exact)}")
    print(f"Matched by fuzzy (Python):      {len(fuzzy)}")
    print(f"Unmatched:                      {len(still_unmatched)}  "
          f"(total {still_unmatched.amount_pln.sum():,.2f} PLN)")
    print(f"Payment match rate:             {100 * (len(matched_exact) + len(fuzzy)) / len(pay):.1f}%\n")


# ------------------------------------------------------------------ SQL results
def run_reconciliation_sql(raw):
    """Create the views from sql/03_reconciliation.sql in SQLite and read the ones we validate."""
    con = sqlite3.connect(":memory:")
    for t in TABLES:
        raw[t].to_sql(t, con, index=False)
    for _, stmt in split_statements(SQL_FILE):
        if stmt.upper().startswith(("CREATE", "DROP")):
            con.execute(stmt)
    views = {v: pd.read_sql(f"SELECT * FROM {v}", con)
             for v in ["v_three_way", "v_duplicate_invoices", "v_payment_status"]}
    con.close()
    return views


# ------------------------------------------------------------------ comparison with the answer key
def build_report(views, fuzzy, still_unmatched, key):
    tw, dups, ps = views["v_three_way"], views["v_duplicate_invoices"], views["v_payment_status"]
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
        tp = len(expected & ids_found)  # one category per record, so the intersection = true positives
        rows.append({"error_type": err, "planted": len(expected), "found": len(ids_found), "correct": tp,
                     "recall_%": round(100 * tp / len(expected), 1) if expected else None,
                     "precision_%": round(100 * tp / len(ids_found), 1) if ids_found else None})
    return pd.DataFrame(rows)


def main():
    strict = "--strict" in sys.argv
    raw, data = load_data()
    pay = guess_suppliers(data["bank_payments"], data["suppliers"])
    matched_exact, fuzzy, still_unmatched = match_payments(pay, data["supplier_invoices"], data["fx_rates"])
    print_matching_summary(pay, matched_exact, fuzzy, still_unmatched)

    report = build_report(run_reconciliation_sql(raw), fuzzy, still_unmatched, data["answer_key"])
    print("=== Validation against answer_key.csv ===")
    print(report.to_string(index=False))
    print("\nNot checked directly: SUPPLIER_NAME_VARIANT (handled by name normalization),")
    print("UNPAID_INVOICE (needs aging vs payment terms), PARTIAL_RECEIPT_OK (correct by design).")

    OUT.mkdir(exist_ok=True)
    report.to_csv(OUT / "validation_report.csv", index=False)
    fuzzy.to_csv(OUT / "fuzzy_matches.csv", index=False)
    print(f"\nSaved {OUT.name}/validation_report.csv and {OUT.name}/fuzzy_matches.csv")

    failed = report[(report["recall_%"].fillna(0) < 100) | (report["precision_%"].fillna(0) < 100)]
    if strict and not failed.empty:
        sys.exit(f"Validation failed for: {', '.join(failed.error_type)}")


if __name__ == "__main__":
    main()
