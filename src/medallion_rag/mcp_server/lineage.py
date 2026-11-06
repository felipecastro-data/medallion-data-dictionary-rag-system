"""Column-level lineage for the lakehouse, read from Unity Catalog's system tables."""

from __future__ import annotations

from medallion_rag.mcp_server.db import ALLOWED_TABLES, CATALOG, run_query

_TRACKED_TABLES = {table for tables in ALLOWED_TABLES.values() for table in tables}

# Only each table's latest build counts, so an earlier version's mappings do not linger. Literal
# columns such as _ingested_at have no source column and are excluded.
_LINEAGE_QUERY = """
    SELECT DISTINCT target_table_name, target_column_name, source_table_full_name, source_column_name
    FROM (
        SELECT *, max(event_time) OVER (PARTITION BY target_table_full_name) AS latest_event
        FROM system.access.column_lineage
        WHERE target_table_catalog = :catalog
          AND target_column_name IS NOT NULL
          AND source_column_name IS NOT NULL
    )
    WHERE event_time = latest_event
    ORDER BY target_table_name, target_column_name, source_table_full_name, source_column_name
"""


def fetch_column_lineage() -> dict[tuple[str, str], list[str]]:
    """Return {(table, column): [source column, ...]} for every tracked lakehouse column.

    Sources inside the lakehouse read as "schema.table.column". Sources outside it keep their
    full name, such as "samples.tpch.customer.c_name".
    """
    prefix = f"{CATALOG}."
    lineage: dict[tuple[str, str], list[str]] = {}
    for row in run_query(_LINEAGE_QUERY, {"catalog": CATALOG}):
        if row["target_table_name"] not in _TRACKED_TABLES:
            continue
        source = f"{row['source_table_full_name'].removeprefix(prefix)}.{row['source_column_name']}"
        lineage.setdefault((row["target_table_name"], row["target_column_name"]), []).append(source)
    return lineage
