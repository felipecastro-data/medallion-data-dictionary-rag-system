"""MCP server exposing Databricks lakehouse metadata as tools.

Tools are consumed by both the metadata-generation subagent and the RAG chatbot.
Queries against 1_bronze/2_silver (TPC-H production scale) always carry a LIMIT;
see get_sample_rows for the hard cap.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from mcp_server.db import (
    CATALOG,
    MAX_SAMPLE_ROWS,
    METADATA_TABLE,
    run_query,
    validate_schema,
    validate_table,
)

mcp = FastMCP("medallion-data-dictionary")


@mcp.tool()
def list_tables(schema: str) -> list[dict[str, Any]]:
    """List table names in a lakehouse schema.

    Args:
        schema: One of "1_bronze", "2_silver", "3_gold".

    Returns:
        Rows with a single "table_name" column, ordered alphabetically.
    """
    validate_schema(schema)
    # information_schema is catalog-scoped in Unity Catalog, so it must be qualified with
    # the catalog name directly (a bare `information_schema.tables` resolves against the
    # connection's default catalog, not `catalog`, regardless of a table_catalog filter).
    query = f"""
        SELECT table_name
        FROM {CATALOG}.information_schema.tables
        WHERE table_catalog = :catalog AND table_schema = :schema
        ORDER BY table_name
    """
    return run_query(query, {"catalog": CATALOG, "schema": schema})


@mcp.tool()
def get_schema(table_name: str, schema: str) -> list[dict[str, Any]]:
    """Get column name, data type, and nullability for a table.

    Args:
        table_name: Table to describe, e.g. "customers".
        schema: One of "1_bronze", "2_silver", "3_gold".

    Returns:
        Rows with "column_name", "data_type", "is_nullable", ordered by column position.
    """
    validate_table(schema, table_name)
    query = f"""
        SELECT column_name, data_type, is_nullable
        FROM {CATALOG}.information_schema.columns
        WHERE table_catalog = :catalog
          AND table_schema = :schema
          AND table_name = :table_name
        ORDER BY ordinal_position
    """
    return run_query(query, {"catalog": CATALOG, "schema": schema, "table_name": table_name})


@mcp.tool()
def get_sample_rows(table_name: str, schema: str, limit: int = 10) -> list[dict[str, Any]]:
    """Return a small sample of rows from a table.

    `limit` is hard-capped at 50 regardless of the requested value, since bronze/silver
    tables are TPC-H production scale and must never be scanned in full.

    Args:
        table_name: Table to sample, e.g. "customers".
        schema: One of "1_bronze", "2_silver", "3_gold".
        limit: Requested row count (default 10, capped at 50).

    Returns:
        Up to `limit` rows as column-keyed dicts.
    """
    validate_table(schema, table_name)
    capped_limit = max(1, min(limit, MAX_SAMPLE_ROWS))
    # schema/table_name are validated against a fixed whitelist above, so it is safe to
    # interpolate them as identifiers here (identifiers cannot be bound as SQL parameters).
    query = f"SELECT * FROM `{CATALOG}`.`{schema}`.`{table_name}` LIMIT {capped_limit}"
    return run_query(query)


@mcp.tool()
def search_metadata(query: str) -> list[dict[str, Any]]:
    """Search the data_dictionary table by simple LIKE match, as a SQL fallback to vector search.

    Matches against table_name, column_name, and description.

    Args:
        query: Substring to search for.

    Returns:
        Matching data_dictionary rows.
    """
    like_pattern = f"%{query}%"
    sql_query = f"""
        SELECT table_name, column_name, description, source, generated_at, embedding_id
        FROM {METADATA_TABLE}
        WHERE table_name LIKE :pattern
           OR column_name LIKE :pattern
           OR description LIKE :pattern
        ORDER BY table_name, column_name
    """
    return run_query(sql_query, {"pattern": like_pattern})


if __name__ == "__main__":
    mcp.run()
