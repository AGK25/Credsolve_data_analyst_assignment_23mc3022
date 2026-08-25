#!/usr/bin/env python3
"""
Stage 1 of the pipeline: RAW ingest.

Design decision (deliberate, documented in docs/DATA_QUALITY_REPORT.md):
every column is loaded as TEXT. We do NOT let the loader cast, coerce or
reject anything. Reason: the assignment requires us to quantify the impact
of our own cleaning decisions, which is impossible if the ingest layer has
already silently dropped malformed rows. A typed loader would, for example,
throw away a payment whose amount is "1,250.00" or a call whose timestamp
carries a "+05:30" offset -- and those rows are exactly the evidence we need.

Raw row counts produced here are the denominator for the whole
Raw -> Rejected/Corrected -> Golden lineage.
"""
import csv
import io
import os
import sys
import psycopg
from pathlib import Path

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
DSN = os.environ.get("PGDSN", "host=127.0.0.1 port=5432 user=postgres dbname=collections")

# Files that are reference material, not data tables.
SKIP = {"README.md"}


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def main() -> int:
    csv.field_size_limit(sys.maxsize)
    files = sorted(p for p in RAW_DIR.iterdir() if p.suffix == ".csv" and p.name not in SKIP)
    if not files:
        print(f"No CSVs found in {RAW_DIR}", file=sys.stderr)
        return 1

    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("CREATE SCHEMA IF NOT EXISTS raw;")
        summary = []

        for path in files:
            table = path.stem
            with path.open(newline="", encoding="utf-8", errors="replace") as fh:
                header = next(csv.reader(fh))

            cols = ", ".join(f"{quote_ident(c)} TEXT" for c in header)
            conn.execute(f"DROP TABLE IF EXISTS raw.{quote_ident(table)} CASCADE;")
            # _ingest_seq preserves original file order so we can identify which
            # copy of a duplicate arrived first -- needed for dedup tie-breaking.
            conn.execute(
                f"CREATE TABLE raw.{quote_ident(table)} "
                f"(_ingest_seq BIGSERIAL PRIMARY KEY, {cols});"
            )

            collist = ", ".join(quote_ident(c) for c in header)
            with conn.cursor() as cur, path.open("rb") as fh:
                with cur.copy(
                    f"COPY raw.{quote_ident(table)} ({collist}) "
                    f"FROM STDIN WITH (FORMAT csv, HEADER true, NULL '')"
                ) as cp:
                    while chunk := fh.read(1 << 20):
                        cp.write(chunk)

            n = conn.execute(f"SELECT count(*) FROM raw.{quote_ident(table)};").fetchone()[0]
            summary.append((table, len(header), n))
            print(f"  raw.{table:<26} {len(header):>2} cols  {n:>9,} rows")

        # Persist the raw census -- this is the anchor for the lineage table.
        conn.execute("DROP TABLE IF EXISTS raw._ingest_census CASCADE;")
        conn.execute(
            "CREATE TABLE raw._ingest_census "
            "(table_name TEXT PRIMARY KEY, n_columns INT, n_rows BIGINT, "
            " ingested_at TIMESTAMPTZ DEFAULT now());"
        )
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO raw._ingest_census (table_name, n_columns, n_rows) VALUES (%s, %s, %s);",
                summary,
            )

        total = sum(r for _, _, r in summary)
        print(f"\n  {len(summary)} tables, {total:,} raw rows ingested (zero rejected by design).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
