"""
Synthetic data generator: Procure-to-Pay Reconciliation
(purchase orders vs goods receipts vs supplier invoices vs bank payments)

Run:     python run.py generate   (or: python python/generate_data.py)
Output (data/ folder):
  suppliers.csv, purchase_orders.csv, goods_receipts.csv,
  supplier_invoices.csv, bank_payments.csv, fx_rates.csv,
  answer_key.csv  <- which errors were planted where. Do NOT use it during the
                     analysis; only at the end, to check that you caught everything.

Requires: pandas, numpy  (pip install pandas numpy)
"""

import os
import random
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

SEED = 42
N_PO = 3000
START, END = date(2026, 1, 1), date(2026, 6, 30)
ROOT = Path(__file__).resolve().parent.parent  # project root, so the script works from any folder
OUT = ROOT / "data"

random.seed(SEED)
np.random.seed(SEED)
os.makedirs(OUT, exist_ok=True)

answer_key = []  # (table, record_id, error_type, note)


def key(table, rec_id, err, note=""):
    answer_key.append({"table": table, "record_id": rec_id, "error_type": err, "note": note})


def rand_date(a=START, b=END):
    return a + timedelta(days=random.randint(0, (b - a).days))


# ---------------------------------------------------------------- suppliers
names = [
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
suppliers = []
for i, n in enumerate(names, 1):
    eur = any(s in n for s in ["GmbH", "S.r.l.", "B.V.", "S.L.", "s.r.o.", "AB", "Kft.", "Hellas"])
    suppliers.append({
        "supplier_id": f"SUP{i:03d}",
        "supplier_name": n,
        "country": "PL" if not eur else "EU",
        "currency": "EUR" if eur else "PLN",
        "payment_terms_days": random.choice([14, 30, 45, 60]),
    })
sup = pd.DataFrame(suppliers)
# a few suppliers get many more orders (realistic Pareto distribution)
weights = np.random.pareto(1.5, len(sup)) + 0.3
weights = weights / weights.sum()

# ---------------------------------------------------------------- EUR/PLN rates
days = pd.date_range(START, END + timedelta(days=90), freq="D")
rate = 4.30 + np.cumsum(np.random.normal(0, 0.006, len(days)))
fx = pd.DataFrame({"rate_date": days.date, "eur_pln": rate.round(4)})
fx_map = dict(zip(fx.rate_date, fx.eur_pln))
STALE_RATE = 4.5000  # "hard-coded" rate from months ago

# ---------------------------------------------------------------- purchase orders
skus = [f"SKU{n:04d}" for n in range(1, 401)]
pos = []
for i in range(1, N_PO + 1):
    s = sup.sample(1, weights=weights, random_state=SEED + i).iloc[0]
    price = round(random.uniform(2, 180), 2)
    pos.append({
        "po_id": f"PO{i:05d}",
        "supplier_id": s.supplier_id,
        "po_date": rand_date(START, END - timedelta(days=20)),
        "sku": random.choice(skus),
        "qty_ordered": random.randint(10, 600),
        "unit_price": price,
        "currency": s.currency,
    })
po = pd.DataFrame(pos)

# ---------------------------------------------------------------- assign planted errors to POs
ids = po.po_id.tolist()
random.shuffle(ids)
take = lambda n: [ids.pop() for _ in range(n)]
err_missing_grn = take(60)        # paid, goods never received
err_qty_over = take(90)           # invoice qty above received qty
err_price = take(60)              # invoice price above PO price
err_dup_invoice = take(30)        # invoice booked and paid twice
err_partial_receipt = take(70)    # partial receipt, invoice matches receipt (NOT an error)
err_unpaid = take(90)             # unpaid invoice (timing, period end)
err_partial_pay = take(30)        # only part of the invoice paid
err_no_ref = take(120)            # transfer without invoice number in title
err_name_typo = take(150)         # supplier name variant in bank
err_receipt_late = take(100)      # receipt recorded after invoice (delay, not an error)
eur_po = set(po.loc[po.currency == "EUR", "po_id"])
err_fx = [p for p in ids if p in eur_po][:40]  # payment converted at a stale rate
for p in err_fx:
    ids.remove(p)

S = lambda lst: set(lst)
E = {k: S(v) for k, v in dict(
    missing_grn=err_missing_grn, qty_over=err_qty_over, price=err_price,
    dup=err_dup_invoice, partial_receipt=err_partial_receipt, unpaid=err_unpaid,
    partial_pay=err_partial_pay, no_ref=err_no_ref, typo=err_name_typo,
    late=err_receipt_late, fx=err_fx).items()}

# ---------------------------------------------------------------- goods receipts (GRN)
grns, invoices, payments = [], [], []
grn_n = inv_n = pay_n = 0
sup_name = dict(zip(sup.supplier_id, sup.supplier_name))
sup_terms = dict(zip(sup.supplier_id, sup.payment_terms_days))


def typo(name):
    """How the same supplier can look on a bank statement."""
    words = name.split(" ")
    variants = [
        name.upper(),                                                    # all caps
        name.replace(" Ltd", " LTD.").replace(" PLC", " P.L.C."),        # different legal-form spelling
        " ".join(words[:-1]),                                            # legal form dropped
        name.replace("&", "and").replace(" Co.", " Company") + ".",     # spelled out
    ]
    return random.choice(variants)


for r in po.itertuples():
    receipt_date = r.po_date + timedelta(days=random.randint(2, 10))
    qty_rec = r.qty_ordered
    if r.po_id in E["partial_receipt"]:
        qty_rec = int(r.qty_ordered * random.uniform(0.6, 0.95))

    if r.po_id not in E["missing_grn"]:
        grn_n += 1
        rd = receipt_date + (timedelta(days=random.randint(15, 30)) if r.po_id in E["late"] else timedelta(0))
        grns.append({"grn_id": f"GRN{grn_n:05d}", "po_id": r.po_id, "receipt_date": rd,
                     "sku": r.sku, "qty_received": qty_rec})
    else:
        key("purchase_orders", r.po_id, "MISSING_GOODS_RECEIPT",
            "Invoice paid, no goods receipt")
    if r.po_id in E["late"]:
        key("goods_receipts", r.po_id, "LATE_RECEIPT_TIMING",
            "Receipt recorded 15-30 days late - timing delay, not a loss")
    if r.po_id in E["partial_receipt"]:
        key("goods_receipts", r.po_id, "PARTIAL_RECEIPT_OK",
            "Received less than ordered, invoice matches receipt - NOT an error")

    # ------------------------------------------------------------ invoice
    inv_qty = qty_rec
    if r.po_id in E["qty_over"]:
        inv_qty = qty_rec + random.randint(5, max(6, qty_rec // 5))
    inv_price = r.unit_price
    if r.po_id in E["price"]:
        inv_price = round(r.unit_price * random.uniform(1.03, 1.15), 2)
    inv_date = receipt_date + timedelta(days=random.randint(0, 5))
    inv_n += 1
    inv_no = f"INV-{inv_date.year}-{inv_date.month:02d}-{inv_n:05d}"
    amount = round(inv_qty * inv_price, 2)
    inv = {"invoice_id": f"INV{inv_n:05d}", "invoice_number": inv_no, "supplier_id": r.supplier_id,
           "po_id": r.po_id, "invoice_date": inv_date, "qty_invoiced": inv_qty,
           "unit_price": inv_price, "amount": amount, "currency": r.currency}
    invoices.append(inv)
    if r.po_id in E["qty_over"]:
        key("supplier_invoices", inv["invoice_id"], "QTY_INVOICED_ABOVE_RECEIVED",
            f"Invoiced {inv_qty}, received {qty_rec}")
    if r.po_id in E["price"]:
        key("supplier_invoices", inv["invoice_id"], "PRICE_ABOVE_PO",
            f"Price {inv_price} vs PO {r.unit_price}")

    inv_list = [inv]
    if r.po_id in E["dup"]:
        inv_n += 1
        dup = dict(inv, invoice_id=f"INV{inv_n:05d}", invoice_date=inv_date + timedelta(days=random.randint(3, 20)))
        invoices.append(dup)
        inv_list.append(dup)
        key("supplier_invoices", dup["invoice_id"], "DUPLICATE_INVOICE",
            f"Same number as {inv['invoice_id']}, booked and paid a second time")

    # ------------------------------------------------------------ payments
    if r.po_id in E["unpaid"]:
        key("supplier_invoices", inv["invoice_id"], "UNPAID_INVOICE",
            "No payment - invoice within or past terms")
        continue
    for k_i, iv in enumerate(inv_list):
        pd_date = iv["invoice_date"] + timedelta(days=sup_terms[r.supplier_id] + random.randint(-3, 3))
        if iv["currency"] == "EUR":
            rate_used = STALE_RATE if r.po_id in E["fx"] else fx_map[pd_date]
            amt_pln = round(iv["amount"] * rate_used, 2)
        else:
            amt_pln = iv["amount"]
        if r.po_id in E["partial_pay"] and k_i == 0:
            amt_pln = round(amt_pln * random.choice([0.5, 0.7, 0.8]), 2)
        pay_n += 1
        ref = "" if r.po_id in E["no_ref"] else iv["invoice_number"]
        name = typo(sup_name[r.supplier_id]) if r.po_id in E["typo"] else sup_name[r.supplier_id]
        payments.append({"payment_id": f"PAY{pay_n:05d}", "payment_date": pd_date,
                         "beneficiary_name": name, "transfer_title": ref,
                         "amount_pln": amt_pln})
        pid = f"PAY{pay_n:05d}"
        if r.po_id in E["fx"]:
            key("bank_payments", pid, "FX_STALE_RATE", f"EUR converted at {STALE_RATE} instead of the daily rate")
        if r.po_id in E["partial_pay"] and k_i == 0:
            key("bank_payments", pid, "PARTIAL_PAYMENT", "Only part of the invoice amount paid")
        if r.po_id in E["no_ref"]:
            key("bank_payments", pid, "MISSING_INVOICE_REF", "Empty transfer title - needs matching by amount/supplier/date")
        if r.po_id in E["typo"]:
            key("bank_payments", pid, "SUPPLIER_NAME_VARIANT", f"Name in bank: {name}")

# ---------------------------------------------------------------- invoices without PO (maverick buying)
for _ in range(45):
    s = sup.sample(1, weights=weights).iloc[0]
    d = rand_date(START, END - timedelta(days=60))
    inv_n += 1
    amt = round(random.uniform(300, 25000), 2)
    inv_no = f"INV-{d.year}-{d.month:02d}-{inv_n:05d}"
    invoices.append({"invoice_id": f"INV{inv_n:05d}", "invoice_number": inv_no, "supplier_id": s.supplier_id,
                     "po_id": None, "invoice_date": d, "qty_invoiced": None, "unit_price": None,
                     "amount": amt, "currency": s.currency})
    key("supplier_invoices", f"INV{inv_n:05d}", "INVOICE_WITHOUT_PO", "Off-process purchase (no purchase order)")
    pd_date = d + timedelta(days=int(s.payment_terms_days))
    pay_n += 1
    payments.append({"payment_id": f"PAY{pay_n:05d}", "payment_date": pd_date,
                     "beneficiary_name": s.supplier_name, "transfer_title": inv_no,
                     "amount_pln": round(amt * (fx_map[pd_date] if s.currency == "EUR" else 1), 2)})

# ---------------------------------------------------------------- payments without invoice (orphans)
for _ in range(15):
    s = sup.sample(1).iloc[0]
    pay_n += 1
    pid = f"PAY{pay_n:05d}"
    payments.append({"payment_id": pid, "payment_date": rand_date(),
                     "beneficiary_name": s.supplier_name,
                     "transfer_title": random.choice(["advance", "URGENT - per phone call", "correction", ""]),
                     "amount_pln": round(random.uniform(1000, 40000), 2)})
    key("bank_payments", pid, "PAYMENT_WITHOUT_INVOICE", "Transfer with no invoice at all (e.g. an 'urgent' transfer)")

# ---------------------------------------------------------------- save
grn = pd.DataFrame(grns)
inv_df = pd.DataFrame(invoices)
inv_df["qty_invoiced"] = inv_df["qty_invoiced"].astype("Int64")  # keep integers despite NULLs
pay = pd.DataFrame(payments).sort_values("payment_date").reset_index(drop=True)
key_df = pd.DataFrame(answer_key)

sup.to_csv(f"{OUT}/suppliers.csv", index=False)
po.to_csv(f"{OUT}/purchase_orders.csv", index=False)
grn.to_csv(f"{OUT}/goods_receipts.csv", index=False)
inv_df.to_csv(f"{OUT}/supplier_invoices.csv", index=False)
pay.to_csv(f"{OUT}/bank_payments.csv", index=False)
fx.to_csv(f"{OUT}/fx_rates.csv", index=False)
key_df.to_csv(f"{OUT}/answer_key.csv", index=False)

print(f"suppliers:         {len(sup):>6}")
print(f"purchase_orders:   {len(po):>6}")
print(f"goods_receipts:    {len(grn):>6}")
print(f"supplier_invoices: {len(inv_df):>6}")
print(f"bank_payments:     {len(pay):>6}")
print(f"answer_key:        {len(key_df):>6}")
print(key_df.error_type.value_counts().to_string())
