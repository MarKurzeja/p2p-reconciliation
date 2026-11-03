"""Run the project's Python workflows from the repository root."""

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def run_script(script: str, *args: str) -> None:
    subprocess.run(
        [sys.executable, str(ROOT / "python" / script), *args],
        cwd=ROOT,
        check=True,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the procure-to-pay reconciliation project workflows."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("demo", help="generate data, validate it, and run SQLite analysis")
    commands.add_parser("generate", help="generate the synthetic reconciliation data")
    commands.add_parser("validate", help="run fuzzy matching and validate against the answer key")

    sql = commands.add_parser("sql", help="run a SQL file against CSV data using SQLite")
    sql.add_argument("sql_file", nargs="?", help="SQL file (default: sql/03_reconciliation.sql)")
    sql.add_argument("data_dir", nargs="?", help="CSV directory (default: data)")
    sql.add_argument("--save", action="store_true", help="save the SQLite database to output/p2p.db")

    download = commands.add_parser("download", help="download and combine DHSC spending data")
    download.add_argument("year", nargs="?", help="year to download (default: 2025)")

    analyze = commands.add_parser("analyze", help="run audit analytics on downloaded DHSC data")
    analyze.add_argument("year", nargs="?", help="year to analyze (default: 2025)")

    xes = commands.add_parser("xes", help="convert a BPI Challenge XES log to CSV")
    xes.add_argument("input_file", help="path to a .xes or .xes.gz file")

    return parser


def main() -> None:
    args = build_parser().parse_args()

    if args.command == "demo":
        run_script("generate_data.py")
        run_script("validate.py")
        run_script("run_sql.py")
    elif args.command == "generate":
        run_script("generate_data.py")
    elif args.command == "validate":
        run_script("validate.py")
    elif args.command == "sql":
        script_args = []
        if args.sql_file is not None:
            script_args.append(args.sql_file)
        if args.data_dir is not None:
            script_args.append(args.data_dir)
        if args.save:
            script_args.append("--save")
        run_script("run_sql.py", *script_args)
    elif args.command == "download":
        run_script("download_gov_spend.py", *([args.year] if args.year else []))
    elif args.command == "analyze":
        run_script("analyze_gov_spend.py", *([args.year] if args.year else []))
    elif args.command == "xes":
        run_script("xes_to_csv.py", args.input_file)


if __name__ == "__main__":
    main()
