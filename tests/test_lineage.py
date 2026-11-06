"""Tests for reading column lineage and exposing it through the MCP server."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from medallion_rag.mcp_server import lineage, server


def lineage_row(table: str, column: str, source_table: str, source_column: str) -> dict[str, str]:
    """Build one row as returned by the lineage query."""
    return {
        "target_table_name": table,
        "target_column_name": column,
        "source_table_full_name": source_table,
        "source_column_name": source_column,
    }


def test_fetch_groups_sources_and_shortens_lakehouse_names(monkeypatch: pytest.MonkeyPatch) -> None:
    """Multi-source columns keep every source, and only lakehouse names lose the catalog."""
    rows = [
        lineage_row("customer_ltv", "avg_order_value", "lakehouse_demo.2_silver.order_items", "net_amount"),
        lineage_row("customer_ltv", "avg_order_value", "lakehouse_demo.2_silver.order_items", "order_id"),
        lineage_row("raw_customers", "c_name", "samples.tpch.customer", "c_name"),
    ]
    monkeypatch.setattr(lineage, "run_query", MagicMock(return_value=rows))

    assert lineage.fetch_column_lineage() == {
        ("customer_ltv", "avg_order_value"): [
            "2_silver.order_items.net_amount",
            "2_silver.order_items.order_id",
        ],
        ("raw_customers", "c_name"): ["samples.tpch.customer.c_name"],
    }


def test_fetch_ignores_untracked_tables(monkeypatch: pytest.MonkeyPatch) -> None:
    """The dictionary table's own lineage is not part of the documented lakehouse."""
    source_table = "lakehouse_demo.information_schema.columns"
    rows = [lineage_row("data_dictionary", "table_name", source_table, "table_name")]
    monkeypatch.setattr(lineage, "run_query", MagicMock(return_value=rows))

    assert lineage.fetch_column_lineage() == {}


def test_tool_returns_only_the_requested_table(monkeypatch: pytest.MonkeyPatch) -> None:
    """The MCP tool narrows the lineage to one table."""
    monkeypatch.setattr(
        server,
        "fetch_column_lineage",
        lambda: {("orders", "clerk"): ["1_bronze.raw_orders.o_clerk"], ("customers", "phone"): ["x"]},
    )

    assert server.get_column_lineage("orders", "2_silver") == [
        {"column_name": "clerk", "derived_from": ["1_bronze.raw_orders.o_clerk"]}
    ]


def test_tool_rejects_tables_outside_the_allowlist() -> None:
    """Validation runs before any query."""
    with pytest.raises(ValueError, match="Unknown table"):
        server.get_column_lineage("secrets", "2_silver")
