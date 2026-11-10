"""
YottaFlex Cart Recovery — Reproducibility Proof
===============================================
Runs the generator TWICE with the same seed, exports CSVs both times,
and compares row counts and SHA-256 checksums.

Expected result: all row counts and all checksums match.

Usage:
  python -m app.repro
  python -m app.repro --seed 20261022
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

OUTPUT_DIR = Path("output")
RUN1_DIR   = OUTPUT_DIR / "repro_run1"
RUN2_DIR   = OUTPUT_DIR / "repro_run2"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def _run_once(seed: int, run_dir: Path, small: bool = False) -> dict[str, str]:
    """Generate data, export CSVs into run_dir, return {filename: sha256}."""
    from .generate import run as _gen
    from sqlalchemy import text
    from .db import engine
    import csv

    run_dir.mkdir(parents=True, exist_ok=True)

    kw: dict = dict(seed=seed, reset=True, export_csv=False)
    if small:
        kw.update(n_customers=50, n_carts=100, n_abandoned=40, n_sends=20)

    _gen(**kw)

    # Export each table to run_dir
    from sqlalchemy import text
    TABLE_PKS = [
        ("customers",          "customer_id"),
        ("consents",           "consent_id"),
        ("consent_events",     "event_id"),
        ("sessions",           "session_id"),
        ("carts",              "cart_id"),
        ("cart_lines",         "cart_line_id"),
        ("cart_status_events", "event_id"),
        ("send_attempts",      "send_id"),
        ("orders",             "order_id"),
        ("attributions",       "attribution_id"),
        ("edge_cases",         "edge_case_id"),
    ]

    checksums: dict[str, str] = {}
    with engine.connect() as conn:
        for table, pk in TABLE_PKS:
            rows = conn.execute(text(
                f"SELECT * FROM {table} ORDER BY {pk}"   # noqa: S608
            )).fetchall()
            fname = f"{table}.csv"
            path  = run_dir / fname
            if rows:
                keys = list(rows[0]._mapping.keys())
                with path.open("w", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=keys)
                    writer.writeheader()
                    for row in rows:
                        writer.writerow(dict(row._mapping))
            else:
                path.write_text("")
            checksums[fname] = _sha256(path)

    return checksums


def run_reproducibility(seed: int = 20261022, small: bool = False) -> dict:
    """
    Execute two independent runs with the same seed.
    Compare checksums and row counts.
    Returns a report dict.
    """
    print(f"\nReproducibility check  (seed={seed})")
    print("=" * 60)

    print("Run 1 ...")
    cs1 = _run_once(seed, RUN1_DIR, small)
    print("Run 2 ...")
    cs2 = _run_once(seed, RUN2_DIR, small)

    matches:    list[dict] = []
    mismatches: list[dict] = []

    all_files = sorted(set(cs1) | set(cs2))
    for fname in all_files:
        h1 = cs1.get(fname, "MISSING")
        h2 = cs2.get(fname, "MISSING")
        ok = (h1 == h2)
        entry = {"file": fname, "run1_sha256": h1, "run2_sha256": h2, "match": ok}
        (matches if ok else mismatches).append(entry)

    status = "PASS" if not mismatches else "FAIL"
    report = {
        "seed":       seed,
        "status":     status,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "matches":    len(matches),
        "mismatches": len(mismatches),
        "details":    matches + mismatches,
    }

    out_path = OUTPUT_DIR / "reproducibility_report.json"
    OUTPUT_DIR.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2))

    print(f"\nResult : {status}")
    print(f"Files checked : {len(all_files)}")
    print(f"Matching      : {len(matches)}")
    print(f"Mismatching   : {len(mismatches)}")
    if mismatches:
        for m in mismatches:
            print(f"  MISMATCH  {m['file']}")
            print(f"    run1: {m['run1_sha256']}")
            print(f"    run2: {m['run2_sha256']}")
    print(f"\nFull report → {out_path}")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reproducibility proof")
    parser.add_argument("--seed",  type=int, default=20261022)
    parser.add_argument("--small", action="store_true")
    args = parser.parse_args()
    run_reproducibility(seed=args.seed, small=args.small)
