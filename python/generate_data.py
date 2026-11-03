"""
Synthetic data generator: Procure-to-Pay Reconciliation
(purchase orders vs goods receipts vs supplier invoices vs bank payments)

Run:     python run.py generate   (or: python python/generate_data.py)
Output (data/ folder):
  suppliers.csv, purchase_orders.csv, goods_receipts.csv,
  supplier_invoices.csv, bank_payments.csv, fx_rates.csv,
  answer_key.csv  <- which errors were planted where. Do NOT use it during the
                     analysis; only at the end, to check that you caught everything.

The random seed is fixed, so every run produces identical files.
"""

import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

SEED = 42
N_PO = 3000
START, END = date(2026, 1, 1), date(2026, 6, 30)
STALE_RATE = 4.5000  # "hard-coded" EUR/PLN rate from months ago
ROOT = Path(__file__).resolve().parent.parent  # project root, so the script works from any folder
OUT = ROOT / "data"

SUPPLIER_NAMES = [
    "Northfield Dairy Ltd", "AgroFresh Produce Ltd", "Golden Crust Bakery Ltd",
    "Orchard Valley Distribution Ltd", "Mountain Spring Beverages PLC", "CleanHome Chemicals Ltd",
    "PackRight Solutions PLC", "Prime Meat Supply Ltd", "Lakeside Confectionery Ltd",
    "Seaside Seafood PLC", "Coffee Import Co. Ltd", "PaperPlus Ltd",
    "Green Valley Vegetables Ltd", "TransLogistics PLC", "Natura Cosmetics Ltd",
    "Silesian Brewery PLC", "Golden Oil Ltd", "Pasta House Distribution Ltd",
    "Orient Spices Ltd", "Arctic Frozen Foods PLC",
    "Nordic Foods GmbH", "Italia Pasta S.r.l.", "Dutch Dairy B.V.", "Iberia Olive S.L.",
    "Bohemia Glass s.r.o.", "Alpen Snacks GmbH", "Baltic Fish AB", "Danube Grain Kft.",
    "Fresh Fruit Hellas S.A.", "Euro Pack GmbH",
    "HygieneMax Ltd", "Fresh Press Juices Ltd", "Farm Fresh Eggs Ltd",
    "Poultry Partners PLC", "World of Tea Ltd", "Royal Sugar PLC",
    "Clear Spring Water Ltd", "Lakeland Cheese Ltd", "PetFood Supplies PLC",
    "Nuts & Dried Fruit Co. Ltd",
]
EUR_MARKERS = ["GmbH", "S.r.l.", "B.V.", "S.L.", "s.r.o.", "AB", "Kft.", "Hellas"]

# planted issues: name -> number of purchase orders affected (taken in this order)
ERROR_SIZES = {
    "missing_grn": 60,       # paid, goods never received
    "qty_over": 90,          # invoice qty above received qty
    "price": 60,             # invoice price above PO price
    "dup": 30,               # invoice booked and paid twice
    "partial_receipt": 70,   # partial receipt, invoice matches receipt (NOT an error)
    "unpaid": 90,            # unpaid invoice (timing, period end)
    "partial_pay": 30,       # only part of the invoice paid
    "no_ref": 120,           # transfer without invoice number in title
    "typo": 150,             # supplier name variant in bank
    "late": 100,             # receipt recorded after invoice (delay, not an error)
}
N_FX_ERRORS = 40             # EUR payments converted at the stale rate
N_INVOICES_WITHOUT_PO = 45
N_ORPHAN_PAYMENTS = 15


def rand_date(a=START, b=END):
    return a + timedelta(days=random.randint(0, (b - a).days))


@dataclass
class Ledger:
    """Documents generated so far, their running numbers and the answer key."""
    grns: list = field(default_factory=list)
    invoices: list = field(default_factory=list)
    payments: list = field(default_factory=list)
    answer_key: list = field(default_factory=list)
    grn_n: int = 0
    inv_n: int = 0
    pay_n: int = 0

    def key(self, table, rec_id, err, note=""):
        self.answer_key.append({"table": table, "record_id": rec_id, "error_type": err, "note": note})

    def next_invoice_id(self):
        self.inv_n += 1
        return f"INV{self.inv_n:05d}"

    def next_payment_id(self):
        self.pay_n += 1
        return f"PAY{self.pay_n:05d}"


# ---------------------------------------------------------------- master data
def make_suppliers():
    rows = []
    for i, n in enumerate(SUPPLIER_NAMES, 1):
        eur = any(s in n for s in EUR_MARKERS)
        rows.append({
            "supplier_id": f"SUP{i:03d}",
            "supplier_name": n,
            "country": "EU" if eur else "PL",
            "currency": "EUR" if eur else "PLN",
            "payment_terms_days": random.choice([14, 30, 45, 60]),
        })
    sup = pd.DataFrame(rows)
    # a few suppliers get many more orders (realistic Pareto distribution)
    weights = np.random.pareto(1.5, len(sup)) + 0.3
    return sup, weights / weights.sum()


def make_fx_rates():
    days = pd.date_range(START, END + timedelta(days=90), freq="D")
    rate = 4.30 + np.cumsum(np.random.normal(0, 0.006, len(days)))
    return pd.DataFrame({"rate_date": days.date, "eur_pln": rate.round(4)})


def make_purchase_orders(sup, weights):
    skus = [f"SKU{n:04d}" for n in range(1, 401)]
    rows = []
    for i in range(1, N_PO + 1):
        s = sup.sample(1, weights=weights, random_state=SEED + i).iloc[0]
        price = round(random.uniform(2, 180), 2)
        rows.append({
            "po_id": f"PO{i:05d}",
            "supplier_id": s.supplier_id,
            "po_date": rand_date(START, END - timedelta(days=20)),
            "sku": random.choice(skus),
            "qty_ordered": random.randint(10, 600),
            "unit_price": price,
            "currency": s.currency,
        })
    return pd.DataFrame(rows)


def assign_errors(po):
    """Pick disjoint sets of POs for each planted issue."""
    ids = po.po_id.tolist()
    random.shuffle(ids)
    errors = {name: {ids.pop() for _ in range(n)} for name, n in ERROR_SIZES.items()}
    eur_po = set(po.loc[po.currency == "EUR", "po_id"])
    fx = [p for p in ids if p in eur_po][:N_FX_ERRORS]
    errors["fx"] = set(fx)
    return errors


def bank_name_variant(name):
    """How the same supplier can look on a bank statement."""
    words = name.split(" ")
    variants = [
        name.upper(),                                                    # all caps
        name.replace(" Ltd", " LTD.").replace(" PLC", " P.L.C."),        # different legal-form spelling
        " ".join(words[:-1]),                                            # legal form dropped
        name.replace("&", "and").replace(" Co.", " Company") + ".",     # spelled out
    ]
    return random.choice(variants)


# ---------------------------------------------------------------- documents per PO
def add_goods_receipt(led, r, E):
    """Record the goods receipt for one PO; return (planned receipt date, qty received)."""
    receipt_date = r.po_date + timedelta(days=random.randint(2, 10))
    qty_rec = r.qty_ordered
    if r.po_id in E["partial_receipt"]:
        qty_rec = int(r.qty_ordered * random.uniform(0.6, 0.95))

    if r.po_id not in E["missing_grn"]:
        led.grn_n += 1
        delay = timedelta(days=random.randint(15, 30)) if r.po_id in E["late"] else timedelta(0)
        led.grns.append({"grn_id": f"GRN{led.grn_n:05d}", "po_id": r.po_id,
                         "receipt_date": receipt_date + delay, "sku": r.sku, "qty_received": qty_rec})
    else:
        led.key("purchase_orders", r.po_id, "MISSING_GOODS_RECEIPT", "Invoice paid, no goods receipt")
    if r.po_id in E["late"]:
        led.key("goods_receipts", r.po_id, "LATE_RECEIPT_TIMING",
                "Receipt recorded 15-30 days late - timing delay, not a loss")
    if r.po_id in E["partial_receipt"]:
        led.key("goods_receipts", r.po_id, "PARTIAL_RECEIPT_OK",
                "Received less than ordered, invoice matches receipt - NOT an error")
    return receipt_date, qty_rec


def add_invoices(led, r, E, receipt_date, qty_rec):
    """Record the invoice for one PO (and its duplicate, if planted); return the invoices."""
    inv_qty = qty_rec
    if r.po_id in E["qty_over"]:
        inv_qty = qty_rec + random.randint(5, max(6, qty_rec // 5))
    inv_price = r.unit_price
    if r.po_id in E["price"]:
        inv_price = round(r.unit_price * random.uniform(1.03, 1.15), 2)
    inv_date = receipt_date + timedelta(days=random.randint(0, 5))
    inv_id = led.next_invoice_id()
    inv = {"invoice_id": inv_id, "invoice_number": f"INV-{inv_date.year}-{inv_date.month:02d}-{led.inv_n:05d}",
           "supplier_id": r.supplier_id, "po_id": r.po_id, "invoice_date": inv_date,
           "qty_invoiced": inv_qty, "unit_price": inv_price, "amount": round(inv_qty * inv_price, 2),
           "currency": r.currency}
    led.invoices.append(inv)
    if r.po_id in E["qty_over"]:
        led.key("supplier_invoices", inv_id, "QTY_INVOICED_ABOVE_RECEIVED", f"Invoiced {inv_qty}, received {qty_rec}")
    if r.po_id in E["price"]:
        led.key("supplier_invoices", inv_id, "PRICE_ABOVE_PO", f"Price {inv_price} vs PO {r.unit_price}")

    inv_list = [inv]
    if r.po_id in E["dup"]:
        dup = dict(inv, invoice_id=led.next_invoice_id(),
                   invoice_date=inv_date + timedelta(days=random.randint(3, 20)))
        led.invoices.append(dup)
        inv_list.append(dup)
        led.key("supplier_invoices", dup["invoice_id"], "DUPLICATE_INVOICE",
                f"Same number as {inv_id}, booked and paid a second time")
    return inv_list


def add_payments(led, r, E, inv_list, sup_name, sup_terms, fx_map):
    """Record the bank payment(s) for one PO's invoices."""
    if r.po_id in E["unpaid"]:
        led.key("supplier_invoices", inv_list[0]["invoice_id"], "UNPAID_INVOICE",
                "No payment - invoice within or past terms")
        return
    for k_i, iv in enumerate(inv_list):
        pay_date = iv["invoice_date"] + timedelta(days=sup_terms[r.supplier_id] + random.randint(-3, 3))
        if iv["currency"] == "EUR":
            rate_used = STALE_RATE if r.po_id in E["fx"] else fx_map[pay_date]
            amt_pln = round(iv["amount"] * rate_used, 2)
        else:
            amt_pln = iv["amount"]
        partial = r.po_id in E["partial_pay"] and k_i == 0
        if partial:
            amt_pln = round(amt_pln * random.choice([0.5, 0.7, 0.8]), 2)
        pid = led.next_payment_id()
        ref = "" if r.po_id in E["no_ref"] else iv["invoice_number"]
        name = bank_name_variant(sup_name[r.supplier_id]) if r.po_id in E["typo"] else sup_name[r.supplier_id]
        led.payments.append({"payment_id": pid, "payment_date": pay_date,
                             "beneficiary_name": name, "transfer_title": ref, "amount_pln": amt_pln})
        if r.po_id in E["fx"]:
            led.key("bank_payments", pid, "FX_STALE_RATE", f"EUR converted at {STALE_RATE} instead of the daily rate")
        if partial:
            led.key("bank_payments", pid, "PARTIAL_PAYMENT", "Only part of the invoice amount paid")
        if r.po_id in E["no_ref"]:
            led.key("bank_payments", pid, "MISSING_INVOICE_REF",
                    "Empty transfer title - needs matching by amount/supplier/date")
        if r.po_id in E["typo"]:
            led.key("bank_payments", pid, "SUPPLIER_NAME_VARIANT", f"Name in bank: {name}")


# ---------------------------------------------------------------- documents outside the PO process
def add_invoices_without_po(led, sup, weights, fx_map):
    """Maverick buying: invoices (and their payments) with no purchase order."""
    for _ in range(N_INVOICES_WITHOUT_PO):
        s = sup.sample(1, weights=weights).iloc[0]
        d = rand_date(START, END - timedelta(days=60))
        inv_id = led.next_invoice_id()
        amt = round(random.uniform(300, 25000), 2)
        inv_no = f"INV-{d.year}-{d.month:02d}-{led.inv_n:05d}"
        led.invoices.append({"invoice_id": inv_id, "invoice_number": inv_no, "supplier_id": s.supplier_id,
                             "po_id": None, "invoice_date": d, "qty_invoiced": None, "unit_price": None,
                             "amount": amt, "currency": s.currency})
        led.key("supplier_invoices", inv_id, "INVOICE_WITHOUT_PO", "Off-process purchase (no purchase order)")
        pay_date = d + timedelta(days=int(s.payment_terms_days))
        led.payments.append({"payment_id": led.next_payment_id(), "payment_date": pay_date,
                             "beneficiary_name": s.supplier_name, "transfer_title": inv_no,
                             "amount_pln": round(amt * (fx_map[pay_date] if s.currency == "EUR" else 1), 2)})


def add_orphan_payments(led, sup):
    """Transfers with no invoice at all (advances, 'urgent' payments)."""
    for _ in range(N_ORPHAN_PAYMENTS):
        s = sup.sample(1).iloc[0]
        pid = led.next_payment_id()
        led.payments.append({"payment_id": pid, "payment_date": rand_date(),
                             "beneficiary_name": s.supplier_name,
                             "transfer_title": random.choice(["advance", "URGENT - per phone call", "correction", ""]),
                             "amount_pln": round(random.uniform(1000, 40000), 2)})
        led.key("bank_payments", pid, "PAYMENT_WITHOUT_INVOICE",
                "Transfer with no invoice at all (e.g. an 'urgent' transfer)")


# ---------------------------------------------------------------- main
def generate():
    """Build all tables. Call order matters: it fixes the random sequence."""
    random.seed(SEED)
    np.random.seed(SEED)

    sup, weights = make_suppliers()
    fx = make_fx_rates()
    fx_map = dict(zip(fx.rate_date, fx.eur_pln))
    po = make_purchase_orders(sup, weights)
    errors = assign_errors(po)

    led = Ledger()
    sup_name = dict(zip(sup.supplier_id, sup.supplier_name))
    sup_terms = dict(zip(sup.supplier_id, sup.payment_terms_days))
    for r in po.itertuples():
        receipt_date, qty_rec = add_goods_receipt(led, r, errors)
        inv_list = add_invoices(led, r, errors, receipt_date, qty_rec)
        add_payments(led, r, errors, inv_list, sup_name, sup_terms, fx_map)
    add_invoices_without_po(led, sup, weights, fx_map)
    add_orphan_payments(led, sup)

    inv = pd.DataFrame(led.invoices)
    inv["qty_invoiced"] = inv["qty_invoiced"].astype("Int64")  # keep integers despite NULLs
    return {
        "suppliers": sup,
        "purchase_orders": po,
        "goods_receipts": pd.DataFrame(led.grns),
        "supplier_invoices": inv,
        "bank_payments": pd.DataFrame(led.payments).sort_values("payment_date").reset_index(drop=True),
        "fx_rates": fx,
        "answer_key": pd.DataFrame(led.answer_key),
    }


def main():
    tables = generate()
    OUT.mkdir(exist_ok=True)
    for name, df in tables.items():
        df.to_csv(OUT / f"{name}.csv", index=False)
    for name in ["suppliers", "purchase_orders", "goods_receipts", "supplier_invoices", "bank_payments", "answer_key"]:
        print(f"{name + ':':<19}{len(tables[name]):>6}")
    print(tables["answer_key"].error_type.value_counts().to_string())


if __name__ == "__main__":
    main()
