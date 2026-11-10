"""
YottaFlex Cart Recovery — CSV/JSON Export Module
=================================================
Exports every table to output/<table>.csv and writes:
  output/source_manifest.json  — file list with SHA-256 checksums
  output/README_data.md        — human-readable data guide
  output/ground_truth.json     — edge-case labels (read from DB)

Usage:
  python -m app.export
"""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from .db import engine

OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)

# Tables and their primary-key columns (export order respects FK deps)
TABLES = [
    ("customers",          "customer_id"),
    ("consents",           "consent_id"),
    ("consent_events",     "event_id"),
    ("sessions",           "session_id"),
    ("carts",              "cart_id"),
    ("cart_lines",         "cart_line_id"),
    ("cart_status_events", "event_id"),
    ("campaigns",          "campaign_id"),
    ("message_templates",  "template_id"),
    ("frequency_cap_logs", "cap_log_id"),
    ("send_attempts",      "send_id"),
    ("orders",             "order_id"),
    ("attributions",       "attribution_id"),
]

GROUND_TRUTH_TABLE = "edge_cases"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def export_table(conn, table: str, pk: str, output_dir: Path) -> tuple[Path, int]:
    """Export a single table to CSV. Returns (path, row_count)."""
    rows = conn.execute(text(f"SELECT * FROM {table} ORDER BY {pk}")).fetchall()   # noqa: S608
    path = output_dir / f"{table}.csv"
    if not rows:
        path.write_text("")
        return path, 0
    keys = list(rows[0]._mapping.keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        for row in rows:
            writer.writerow(dict(row._mapping))
    return path, len(rows)


def export_ground_truth(conn, output_dir: Path) -> tuple[Path, Path]:
    """Export edge_cases to ground_truth.csv and ground_truth.json."""
    rows = conn.execute(text(
        "SELECT * FROM edge_cases ORDER BY edge_case_id"
    )).fetchall()
    # CSV
    csv_path = output_dir / "ground_truth.csv"
    if rows:
        keys = list(rows[0]._mapping.keys())
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=keys)
            writer.writeheader()
            for row in rows:
                writer.writerow(dict(row._mapping))
    else:
        csv_path.write_text("")
    # JSON
    json_path = output_dir / "ground_truth.json"
    json_path.write_text(
        json.dumps([dict(r._mapping) for r in rows], default=str, indent=2)
    )
    return csv_path, json_path


def write_readme(output_dir: Path, manifest: dict) -> Path:
    path = output_dir / "README_data.md"
    lines = [
        "# YottaFlex Cart Recovery — Exported Dataset",
        "",
        f"Generated at : `{manifest['generated_at']}`",
        f"Seed         : `{manifest['seed']}`",
        f"Generator    : `{manifest['generator']}`",
        "",
        "## Scale",
        "",
        f"| Entity    | Count |",
        f"|-----------|-------|",
    ]
    for k, v in manifest["scale"].items():
        lines.append(f"| {k:<9} | {v:>5} |")
    lines += [
        "",
        "## Files",
        "",
        "| File | SHA-256 |",
        "|------|---------|",
    ]
    for fname, chk in manifest["files"].items():
        lines.append(f"| `{fname}` | `{chk[:16]}…` |")
    lines += [
        "",
        "## Ground Truth",
        "",
        "`ground_truth.csv` and `ground_truth.json` contain labelled anomalies.",
        "**Never merge these files into input features** — they are evaluation-only.",
        "",
        "## Licence / Provenance",
        "",
        "All data is fully synthetic. No real customer information is included.",
        "Email domains use the `.test` TLD (RFC 2606). Phone numbers are fictitious.",
        "No external datasets were used.",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def run_export() -> dict:
    checksums: dict[str, str] = {}
    row_counts: dict[str, int] = {}

    with engine.connect() as conn:
        for table, pk in TABLES:
            try:
                path, cnt = export_table(conn, table, pk, OUTPUT_DIR)
                checksums[f"{table}.csv"] = _sha256(path)
                row_counts[table] = cnt
                print(f"  exported {table:<26} {cnt:>8,} rows  → {path}")
            except Exception as e:
                print(f"  ERROR exporting {table}: {e}")

        csv_gt, json_gt = export_ground_truth(conn, OUTPUT_DIR)
        checksums["ground_truth.csv"] = _sha256(csv_gt)
        print(f"  exported ground_truth            → {csv_gt}")

    # Try to read seed from manifest if it already exists
    manifest_path = OUTPUT_DIR / "source_manifest.json"
    seed = 20261022
    if manifest_path.exists():
        try:
            existing = json.loads(manifest_path.read_text())
            seed = existing.get("seed", seed)
        except Exception:
            pass

    manifest = {
        "generator":    "app/generate.py",
        "export_module":"app/export.py",
        "seed":          seed,
        "generated_at":  datetime.now(timezone.utc).isoformat(),
        "scale":         {k: v for k, v in row_counts.items()
                          if k in ("customers","carts","send_attempts","orders")},
        "files":         checksums,
        "note":          "All data is fully synthetic. Email domains use .test TLD.",
        "lawful_provenance": {
            "external_data": "none",
            "generation":    "algorithmic, fixed seed",
            "personal_data": "none — all names, emails and phones are fictitious",
        },
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))
    write_readme(OUTPUT_DIR, manifest)

    print(f"\nManifest → {manifest_path}")
    return {"status": "ok", "row_counts": row_counts, "checksums": checksums}


if __name__ == "__main__":
    print("Exporting tables to output/ ...")
    result = run_export()
    print("\nExport complete.")
