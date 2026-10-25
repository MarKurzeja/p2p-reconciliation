"""
Run a SQL file against the CSV tables without installing a database server.
Loads every CSV from a data folder into SQLite and prints each query result.

Usage (from the project root):
    python python/run_sql.py                          # sql/reconciliation.sql on data/
    python python/run_sql.py sql/bpi_compliance.sql bpi_data
    python python/run_sql.py --save                   # also writes p2p.db (open it in
                                                      # DB Browser for SQLite or Power BI)

Requires: pandas
"""

import glob
import os
import re
import sqlite3
import sys

import pandas as pd

args = [a for a in sys.argv[1:] if not a.startswith("--")]
sql_file = args[0] if len(args) > 0 else "sql/reconciliation.sql"
data_dir = args[1] if len(args) > 1 else "data"
save = "--save" in sys.argv
rows_to_show = 15

pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 20)

con = sqlite3.connect("p2p.db" if save else ":memory:")
csvs = sorted(glob.glob(os.path.join(data_dir, "*.csv")))
if not csvs:
    sys.exit(f"No CSV files in '{data_dir}/'. Run python/generate_data.py first.")
for path in csvs:
    table = os.path.splitext(os.path.basename(path))[0]
    if table == "answer_key":  # keep the answer key out of the analysis
        continue
    pd.read_csv(path).to_sql(table, con, index=False, if_exists="replace")
print(f"Loaded {len(csvs)} CSV files from {data_dir}/ into SQLite\n")

statements, buf, title, last_title = [], [], "", ""
for line in open(sql_file, encoding="utf-8"):
    code, _, comment = line.partition("--")
    comment = comment.strip(" -\n")
    if comment and not set(comment) <= set("=-") and not code.strip():
        last_title = comment          # a full-line comment = title for the next query
    if code.strip() and not buf:
        title = last_title
    while ";" in code:                # a statement ends at ';' outside comments
        before, code = code.split(";", 1)
        buf.append(before)
        statements.append((title, "\n".join(buf).strip()))
        buf, title = [], last_title
    if code.strip():
        buf.append(code.rstrip())

for title, code in statements:
    if not code:
        continue
    if re.match(r"^(SELECT|WITH)\b", code, re.I):
        df = pd.read_sql(code, con)
        print("=" * 80)
        print(title or code.splitlines()[0])
        print("-" * 80)
        print(df.head(rows_to_show).to_string(index=False) if len(df) else "(no rows)")
        print(f"({len(df)} rows)\n")
    else:
        con.execute(code)

if save:
    con.commit()
    print("Saved database to p2p.db")
