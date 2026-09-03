"""Smoke test: call each MCP tool once against the real Databricks connection.

Not a unit test suite — run directly (`python -m mcp_server.test_server`) to confirm
the server's tools work end to end against the live lakehouse_demo catalog.
"""

from __future__ import annotations

from mcp_server.db import DatabricksConnectionError
from mcp_server.server import get_sample_rows, get_schema, list_tables, search_metadata


def main() -> None:
    """Run each tool once and print a short summary of the result."""
    try:
        print("=== list_tables('2_silver') ===")
        tables = list_tables("2_silver")
        for row in tables:
            print(f"  {row['table_name']}")

        print("\n=== get_schema('customers', '2_silver') ===")
        columns = get_schema("customers", "2_silver")
        for row in columns:
            print(f"  {row['column_name']:<20} {row['data_type']:<15} nullable={row['is_nullable']}")

        print("\n=== get_sample_rows('customers', '2_silver', limit=5) ===")
        rows = get_sample_rows("customers", "2_silver", limit=5)
        print(f"  returned {len(rows)} row(s)")
        if rows:
            print(f"  first row: {rows[0]}")

        print("\n=== get_sample_rows over-limit request (limit=1000, expect capped at 50) ===")
        capped_rows = get_sample_rows("raw_customers", "1_bronze", limit=1000)
        print(f"  returned {len(capped_rows)} row(s) (must be <= 50)")
        assert len(capped_rows) <= 50, "LIMIT cap was not enforced!"

        print("\n=== search_metadata('customer') ===")
        matches = search_metadata("customer")
        print(f"  {len(matches)} match(es)")
        for row in matches[:5]:
            print(f"  {row['table_name']}.{row['column_name']}")

        print("\nAll tools executed successfully.")

    except DatabricksConnectionError as exc:
        print(f"\nDatabricks connection/query error: {exc}")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
