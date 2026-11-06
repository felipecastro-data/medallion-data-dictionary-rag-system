"""Detect schema drift in lakehouse_demo and block re-indexing until it has been reviewed.

Queries information_schema.columns for every tracked table across 1_bronze, 2_silver, and
3_gold, hashes the resulting table/column/type structure, and compares it against a saved
baseline. If the structure changed, logs which columns changed and exits 1. The baseline
stays in place until the change is accepted, so every later run keeps failing rather than
passing silently.

This is meant to be run manually or via a scheduled task (e.g. cron), not continuously.
medallion-index also runs this check first.

Usage:
    medallion-schema-drift [--accept]

--accept records the current schema as the new baseline after the changes are reviewed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from medallion_rag.logging_config import configure_logging
from medallion_rag.mcp_server.db import ALLOWED_TABLES, CATALOG, run_query

logger = logging.getLogger(__name__)

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


def run(accept: bool = False) -> int:
    """Compare the current schema against the baseline and return an exit code (1 on unaccepted drift)."""
    current_columns = fetch_current_columns()
    current_hash = hash_columns(current_columns)
    snapshot = load_snapshot()

    if snapshot is None:
        logger.info("No snapshot found at %s. Saving current schema as the baseline.", SNAPSHOT_PATH)
        save_snapshot(current_columns, current_hash)
        return 0

    if snapshot["hash"] == current_hash:
        logger.info("No schema changes detected.")
        return 0

    logger.warning("Schema change detected in lakehouse_demo:")
    for line in diff_columns(snapshot["columns"], current_columns):
        logger.warning(line)

    if accept:
        save_snapshot(current_columns, current_hash)
        logger.info("Accepted the current schema as the new baseline.")
        return 0

    logger.error("Review these changes, then run `medallion-schema-drift --accept` to continue.")
    return 1


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--accept",
        action="store_true",
        help="Record the current schema as the new baseline.",
    )
    args = parser.parse_args()
    configure_logging()
    return run(accept=args.accept)


if __name__ == "__main__":
    raise SystemExit(main())
