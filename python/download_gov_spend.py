"""
Download real UK government supplier payments (DHSC "spending over £25,000")
for one calendar year and combine them into one clean table.

Source: GOV.UK transparency data, Department of Health and Social Care
https://www.gov.uk/government/collections/dhsc-spending-over-25000
Licence: Open Government Licence v3.0

Run from the project root:
    python python/download_gov_spend.py            # 2025
    python python/download_gov_spend.py 2024       # another year

Output:
    real_data/raw/dhsc_<year>_<mm>.csv     one file per month, as published
    real_data/dhsc_spend_<year>.csv        all months, standardised columns

Requires: pandas (only the standard library is used for downloading)
"""

import csv
import io
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

import pandas as pd

YEAR = int(sys.argv[1]) if len(sys.argv) > 1 else 2025
MONTHS = ["january", "february", "march", "april", "may", "june", "july",
          "august", "september", "october", "november", "december"]
API = "https://www.gov.uk/api/content/government/publications/dhsc-spending-over-25000-{m}-{y}"
PAGE = "https://www.gov.uk/government/publications/dhsc-spending-over-25000-{m}-{y}"
RAW_DIR = "real_data/raw"
OUT = f"real_data/dhsc_spend_{YEAR}.csv"

# standard column names, in the order GOV.UK spend files normally use
STANDARD = ["department_family", "entity", "date", "expense_type", "expense_area",
            "supplier", "transaction_number", "amount", "description",
            "supplier_postcode", "supplier_type"]
# map of published header words -> standard name
HEADER_MAP = {
    "department family": "department_family", "entity": "entity", "date": "date",
    "expense type": "expense_type", "expense area": "expense_area", "supplier": "supplier",
    "supplier name": "supplier", "transaction number": "transaction_number",
    "transaction no": "transaction_number", "amount": "amount", "amount (£)": "amount",
    "value": "amount", "description": "description", "supplier post code": "supplier_postcode",
    "supplier postcode": "supplier_postcode", "postcode": "supplier_postcode",
    "supplier type": "supplier_type",
}
HEADERS = {"User-Agent": "Mozilla/5.0 (portfolio data project)"}


def get(url):
    """Download a URL. On macOS, Python often lacks SSL certificates, so fall back to curl."""
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read()
    except Exception:
        res = subprocess.run(["curl", "-sSfL", "-A", HEADERS["User-Agent"], url],
                             capture_output=True, timeout=120)
        if res.returncode != 0:
            raise RuntimeError(res.stderr.decode(errors="replace").strip() or f"curl exit {res.returncode}")
        return res.stdout


def csv_links(month, year):
    """Ask the GOV.UK Content API which CSV files are attached to the monthly page."""
    data = json.loads(get(API.format(m=month, y=year)))
    links = []
    for att in data.get("details", {}).get("attachments", []):
        url = att.get("url", "")
        ctype = att.get("content_type", "")
        if url.lower().endswith(".csv") or "csv" in ctype:
            links.append(url)
    if not links:  # fallback: read CSV links straight from the HTML page
        html = get(PAGE.format(m=month, y=year)).decode("utf-8", errors="replace")
        links = sorted(set(re.findall(r"https://assets\.publishing\.service\.gov\.uk/[^\"'\s]+?\.csv", html)))
    return links


def read_any_encoding(raw):
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def tidy(text, source_month):
    """Find the header row (some files have title lines above it) and standardise columns."""
    lines = list(csv.reader(io.StringIO(text)))
    width = max((len(r) for r in lines), default=0)
    rows = pd.DataFrame([r + [""] * (width - len(r)) for r in lines], dtype=str)
    header_idx = None
    for i in range(min(15, len(rows))):
        cells = [c.strip().lower() for c in rows.iloc[i]]
        if any("supplier" in c for c in cells) and any(c in ("amount", "amount (£)", "value") or "amount" in c for c in cells):
            header_idx = i
            break
    if header_idx is not None:
        names = [HEADER_MAP.get(c.strip().lower(), re.sub(r"\W+", "_", c.strip().lower()).strip("_"))
                 for c in rows.iloc[header_idx]]
        df = rows.iloc[header_idx + 1:].copy()
        df.columns = names
    else:  # no header: assume the standard GOV.UK column order
        df = rows.copy()
        df.columns = STANDARD[: df.shape[1]] + [f"extra_{i}" for i in range(df.shape[1] - len(STANDARD))]

    df = df.loc[:, ~df.columns.duplicated()]
    for col in STANDARD:
        if col not in df.columns:
            df[col] = ""
    df = df[STANDARD].copy()

    # clean types
    df["amount"] = pd.to_numeric(df["amount"].str.replace(r"[£,\s]", "", regex=True), errors="coerce")
    df["date"] = pd.to_datetime(df["date"].str.strip(), dayfirst=True, errors="coerce").dt.date
    for col in ["entity", "expense_type", "expense_area", "supplier", "description", "supplier_type"]:
        df[col] = df[col].str.strip()
    # drop blank and total lines (no date and no transaction number)
    df = df[df["amount"].notna() & (df["date"].notna() | df["transaction_number"].str.strip().ne(""))]
    # personal data is withheld in the published files - keep the payment, flag the supplier
    df["supplier_redacted"] = df["supplier"].isin(["", "Not Available", "N/A", "REDACTED", "Redacted"])
    df.loc[df["supplier_redacted"], "supplier"] = "(redacted)"
    df["source_month"] = source_month
    return df


def main():
    os.makedirs(RAW_DIR, exist_ok=True)
    frames, missing = [], []
    for i, month in enumerate(MONTHS, 1):
        label = f"{YEAR}-{i:02d}"
        try:
            links = csv_links(month, YEAR)
        except Exception as e:  # page not published yet, or network error
            missing.append(f"{label} ({str(e)[:80]})")
            continue
        if not links:
            missing.append(f"{label} (no CSV attached)")
            continue
        for j, url in enumerate(links):
            raw = get(url)
            suffix = "" if j == 0 else f"_{j + 1}"
            path = f"{RAW_DIR}/dhsc_{YEAR}_{i:02d}{suffix}.csv"
            with open(path, "wb") as f:
                f.write(raw)
            df = tidy(read_any_encoding(raw), label)
            frames.append(df)
            print(f"{label}: {len(df):>5} payments  {df['amount'].sum():>16,.0f} GBP  <- {url.rsplit('/', 1)[-1]}")
        time.sleep(0.5)  # be polite to the server

    if not frames:
        print("Months not available:")
        for m in missing:
            print("  ", m)
        sys.exit("Nothing downloaded.")
    all_df = pd.concat(frames, ignore_index=True)
    all_df.insert(0, "payment_id", [f"P{n:06d}" for n in range(1, len(all_df) + 1)])
    all_df.to_csv(OUT, index=False)

    print(f"\nSaved {OUT}: {len(all_df):,} payments, {all_df['supplier'].nunique():,} suppliers, "
          f"{all_df['amount'].sum():,.0f} GBP")
    print(f"Rows with an unreadable date: {all_df['date'].isna().sum()}")
    if missing:
        print("Months not available:", ", ".join(missing))


if __name__ == "__main__":
    main()
