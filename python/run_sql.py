"""
Run a SQL file against the CSV tables without installing a database server.
Loads every CSV from a data folder into SQLite and prints each query result.

Usage:
    python run.py sql                           # sql/03_reconciliation.sql on data/
    python run.py sql <file.sql> <data_dir>     # any SQL file on any CSV folder
    python run.py sql --save                    # also writes output/p2p.db (open it in
                                                # DB Browser for SQLite or Power BI)
"""

import re
import sqlite3
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent  # project root, so the script works from any folder
DEFAULT_SQL = ROOT / "sql" / "03_reconciliation.sql"
DEFAULT_DATA = ROOT / "data"
DB_PATH = ROOT / "output" / "p2p.db"
ROWS_TO_SHOW = 15


def split_statements(sql_file):
    """Split a SQL file into (title, statement) pairs.

    Comments are removed; a full-line comment becomes the title of the next statement.
    A statement ends at ';' outside a comment.
    """
    statements, buf, title, last_title = [], [], "", ""
    with open(sql_file, encoding="utf-8") as f:
        for line in f:
            code, _, comment = line.partition("--")
            comment = comment.strip(" -\n")
            if comment and not set(comment) <= set("=-") and not code.strip():
                last_title = comment
            if code.strip() and not buf:
                title = last_title
            while ";" in code:
                before, code = code.split(";", 1)
                buf.append(before)
                statements.append((title, "\n".join(buf).strip()))
                buf, title = [], last_title
            if code.strip():
                buf.append(code.rstrip())
    return [(t, s) for t, s in statements if s]


def load_csvs(con, data_dir):
    """Load every CSV in data_dir as a table (the answer key is kept out of the analysis)."""
    paths = sorted(p for p in Path(data_dir).glob("*.csv") if p.stem != "answer_key")
    if not paths:
        sys.exit(f"No CSV files in '{data_dir}'. Run `python run.py generate` first.")
    for path in paths:
        pd.read_csv(path).to_sql(path.stem, con, index=False, if_exists="replace")
    return len(paths)


def display_path(path):
    path = Path(path).resolve()
    return path.relative_to(ROOT) if path.is_relative_to(ROOT) else path


def run(sql_file, con):
    """Execute every statement; print the result of each SELECT / WITH query."""
    for title, code in split_statements(sql_file):
        if re.match(r"^(SELECT|WITH)\b", code, re.I):
            df = pd.read_sql(code, con)
            print("=" * 80)
            print(title or code.splitlines()[0])
            print("-" * 80)
            print(df.head(ROWS_TO_SHOW).to_string(index=False) if len(df) else "(no rows)")
            print(f"({len(df)} rows)\n")
        else:
            con.execute(code)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    sql_file = args[0] if len(args) > 0 else DEFAULT_SQL
    data_dir = args[1] if len(args) > 1 else DEFAULT_DATA
    save = "--save" in sys.argv

    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 20)

    if save:
        DB_PATH.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(DB_PATH if save else ":memory:")
    n = load_csvs(con, data_dir)
    print(f"Loaded {n} CSV files from {display_path(data_dir)}/ into SQLite\n")
    run(sql_file, con)
    if save:
        con.commit()
        print(f"Saved database to {display_path(DB_PATH)}")
    con.close()


if __name__ == "__main__":
    main()
