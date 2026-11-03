"""
Convert the BPI Challenge 2019 log (.xes or .xes.gz) into CSV tables
ready for SQL / Power BI.

Run:
    python run.py xes "BPI_Challenge_2019.xes"

Requires only pandas (the XML parser is built into Python; pm4py is not needed).
The file has ~1.6M events: conversion takes a few minutes and needs ~2-4 GB of RAM.

Output (bpi_data/ folder):
  cases.csv            one row = one purchase order item (case) + its attributes
  events.csv           one row = one event (activity, timestamp, user, value)
  po_items.csv         PO items with creation date and value
  goods_receipts.csv   "Record Goods Receipt" events
  invoices.csv         "Record Invoice Receipt" events
  invoice_clearing.csv "Clear Invoice" events (clearing = proxy for payment)
"""

import gzip
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent  # project root, so the script works from any folder

OUT = str(ROOT / "bpi_data")

# column names without spaces or dots, easier in SQL
RENAME = {
    "concept:name": "case_id",
    "Purchasing Document": "purchasing_document",
    "Item": "item",
    "Item Type": "item_type",
    "GR-Based Inv. Verif.": "gr_based_inv_verif",
    "Goods Receipt": "goods_receipt_required",
    "Source": "source_system",
    "Doc. Category name": "doc_category",
    "Company": "company",
    "Spend classification text": "spend_classification",
    "Spend area text": "spend_area",
    "Sub spend area text": "sub_spend_area",
    "Vendor": "vendor",
    "Name": "vendor_name",
    "Document Type": "document_type",
    "Item Category": "item_category",
}


def local(tag):
    return tag.split("}")[-1]


def attrs_of(elem):
    """Return a dict of XES attributes (string/date/float/int/boolean) from direct children."""
    out = {}
    for child in elem:
        t = local(child.tag)
        if t in ("string", "date", "float", "int", "boolean", "id"):
            out[child.get("key")] = child.get("value")
    return out


def parse(path):
    opener = gzip.open if path.endswith(".gz") else open
    cases, events = [], []
    with opener(path, "rb") as f:
        for ev, elem in ET.iterparse(f, events=("end",)):
            if local(elem.tag) != "trace":
                continue
            c = attrs_of(elem)
            cid = c.get("concept:name")
            cases.append(c)
            for e in elem:
                if local(e.tag) != "event":
                    continue
                a = attrs_of(e)
                events.append({
                    "case_id": cid,
                    "activity": a.get("concept:name"),
                    "timestamp": a.get("time:timestamp"),
                    "user": a.get("User", a.get("org:resource")),
                    "net_worth_eur": a.get("Cumulative net worth (EUR)"),
                })
            elem.clear()  # frees memory - essential for a large file
            if len(cases) % 25000 == 0:
                print(f"  ...{len(cases):,} cases")
    return pd.DataFrame(cases), pd.DataFrame(events)


def main():
    if len(sys.argv) < 2:
        sys.exit("Usage: python xes_to_csv.py BPI_Challenge_2019.xes")
    os.makedirs(OUT, exist_ok=True)
    print("Reading XES...")
    cases, events = parse(sys.argv[1])

    cases = cases.rename(columns=RENAME)
    cases.columns = [c.lower().replace(" ", "_").replace(".", "").replace(":", "_") for c in cases.columns]
    events["timestamp"] = pd.to_datetime(events.timestamp, utc=True, errors="coerce")
    events["net_worth_eur"] = pd.to_numeric(events.net_worth_eur, errors="coerce")
    events = events.sort_values(["case_id", "timestamp"])

    cases.to_csv(f"{OUT}/cases.csv", index=False)
    events.to_csv(f"{OUT}/events.csv", index=False)

    # ---- P2P-style tables (same idea as the synthetic project)
    create = (events[events.activity == "Create Purchase Order Item"]
              .groupby("case_id").agg(po_created_at=("timestamp", "min"),
                                      po_value_eur=("net_worth_eur", "first")).reset_index())
    po_items = cases.merge(create, on="case_id", how="left")
    po_items.to_csv(f"{OUT}/po_items.csv", index=False)

    for activity, name in [("Record Goods Receipt", "goods_receipts"),
                           ("Record Invoice Receipt", "invoices"),
                           ("Clear Invoice", "invoice_clearing")]:
        t = events[events.activity == activity].copy()
        t["seq"] = t.groupby("case_id").cumcount() + 1  # sequence number within the item
        t.drop(columns="activity").to_csv(f"{OUT}/{name}.csv", index=False)

    print(f"\nDone -> folder {OUT}/")
    print(f"  PO items (cases): {len(cases):,}")
    print(f"  events:           {len(events):,}")
    print("\nMost frequent activities:")
    print(events.activity.value_counts().head(12).to_string())
    if "item_category" in cases:
        print("\nMatching categories (item_category):")
        print(cases.item_category.value_counts().to_string())


if __name__ == "__main__":
    main()
