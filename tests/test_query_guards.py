"""Tests for the guards that keep MCP tools inside the tracked tables and off full table scans."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock

import pytest

from medallion_rag.mcp_server import server


@pytest.fixture
def query(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace run_query so no test can reach Databricks, and return it for inspection."""
    mock = MagicMock(return_value=[])
    monkeypatch.setattr(server, "run_query", mock)
    return mock


@pytest.mark.parametrize(
    ("requested", "applied"), [(10, 10), (50, 50), (51, 50), (10_000_000, 50), (0, 1), (-5, 1)]
)
def test_sample_rows_limit_is_always_between_1_and_50(query: MagicMock, requested: int, applied: int) -> None:
    """No requested limit can turn a sample into a scan of a production-scale table."""
    server.get_sample_rows("raw_orders", "1_bronze", limit=requested)

    assert query.call_args.args[0].endswith(f"LIMIT {applied}")


@pytest.mark.parametrize(
    ("table_name", "schema"),
    [
        ("customers", "1_bronze"),  # a real table, in the wrong schema
        ("not_a_table", "2_silver"),
        ("orders` WHERE 1=1 --", "2_silver"),
        ("orders", "information_schema"),
    ],
)
@pytest.mark.parametrize("tool", [server.get_sample_rows, server.get_schema, server.get_column_lineage])
def test_tools_reject_tables_outside_the_allowlist(
    query: MagicMock, tool: Callable[[str, str], Any], table_name: str, schema: str
) -> None:
    """Table and schema names are interpolated into SQL, so they must be validated before any query."""
    with pytest.raises(ValueError):
        tool(table_name, schema)

    query.assert_not_called()


def test_list_tables_rejects_an_unknown_schema(query: MagicMock) -> None:
    """A schema outside the lakehouse never reaches the database."""
    with pytest.raises(ValueError):
        server.list_tables("system")

    query.assert_not_called()
