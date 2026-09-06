"""Detect schema drift in lakehouse_demo and flag when the embedding index is stale.

Queries information_schema.columns for every tracked table across 1_bronze, 2_silver, and
3_gold, hashes the resulting table/column/type structure, and compares it against a
snapshot saved on the previous run. If the structure changed, prints which tables/columns
changed and points at build_index.py to rebuild the embedding index (it does not run it).

This is meant to be run manually or via a scheduled task (e.g. cron), not continuously.

Usage:
    python -m hooks.detect_schema_change
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp_server.db import ALLOWED_TABLES, CATALOG, run_query

SNAPSHOT_PATH = Path(__file__).parent / "schema_snapshot.json"


def fetch_current_columns() -> dict[str, str]:
    """Return {"schema.table.column": data_type} for every tracked table in the catalog."""
    columns: dict[str, str] = {}
    for schema, tables in ALLOWED_TABLES.items():
        query = f"""
            SELECT table_name, column_name, data_type
            FROM {CATALOG}.information_schema.columns
            WHERE table_catalog = :catalog AND table_schema = :schema
            ORDER BY table_name, ordinal_position
        """
        rows = run_query(query, {"catalog": CATALOG, "schema": schema})
        for row in rows:
            if row["table_name"] not in tables:
                continue
            key = f"{schema}.{row['table_name']}.{row['column_name']}"
            columns[key] = row["data_type"]
    return columns


def hash_columns(columns: dict[str, str]) -> str:
    """Compute a stable hash of the table/column/type structure."""
    canonical = json.dumps(columns, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_snapshot() -> dict[str, Any] | None:
    """Load the previously saved snapshot, or None if this is the first run."""
    if not SNAPSHOT_PATH.exists():
        return None
    return json.loads(SNAPSHOT_PATH.read_text())


def save_snapshot(columns: dict[str, str], structure_hash: str) -> None:
    """Persist the current schema structure as the new baseline snapshot."""
    snapshot = {
        "hash": structure_hash,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "columns": columns,
    }
    SNAPSHOT_PATH.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")


def diff_columns(old: dict[str, str], new: dict[str, str]) -> list[str]:
    """Return human-readable lines describing added, removed, and type-changed columns."""
    lines: list[str] = []
    for key in sorted(set(new) - set(old)):
        lines.append(f"  + added    {key} ({new[key]})")
    for key in sorted(set(old) - set(new)):
        lines.append(f"  - removed  {key} ({old[key]})")
    for key in sorted(set(old) & set(new)):
        if old[key] != new[key]:
            lines.append(f"  ~ retyped  {key}: {old[key]} -> {new[key]}")
    return lines


def run() -> None:
    """Compare the current lakehouse schema against the saved snapshot and report drift."""
    current_columns = fetch_current_columns()
    current_hash = hash_columns(current_columns)
    snapshot = load_snapshot()

    if snapshot is None:
        print(f"No snapshot found at {SNAPSHOT_PATH}. Saving current schema as the baseline.")
        save_snapshot(current_columns, current_hash)
        return

    if snapshot["hash"] == current_hash:
        print("No schema changes detected.")
        return

    print("Schema change detected in lakehouse_demo:")
    for line in diff_columns(snapshot["columns"], current_columns):
        print(line)
    print()
    print(
        "The embedding index may now be out of date. Re-run `python -m embeddings.build_index` "
        "to rebuild it."
    )

    save_snapshot(current_columns, current_hash)


if __name__ == "__main__":
    run()
