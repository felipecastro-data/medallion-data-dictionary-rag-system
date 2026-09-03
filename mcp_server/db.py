"""Databricks SQL connection management shared by all MCP tools."""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator

from databricks import sql
from databricks.sql.client import Connection
from dotenv import load_dotenv

load_dotenv()

CATALOG = "lakehouse_demo"
METADATA_TABLE = f"{CATALOG}.3_gold.data_dictionary"
MAX_SAMPLE_ROWS = 50

# The only schemas/tables this project's tools are allowed to touch. Used to validate
# user-supplied schema/table names before they are interpolated into SQL identifiers
# (values like WHERE table_schema = :schema are parameterized instead, see run_query).
ALLOWED_TABLES: dict[str, set[str]] = {
    "1_bronze": {"raw_customers", "raw_orders", "raw_lineitem"},
    "2_silver": {"customers", "orders", "order_items"},
    "3_gold": {"daily_sales_summary", "customer_ltv"},
}
ALLOWED_SCHEMAS: set[str] = set(ALLOWED_TABLES)


class DatabricksConnectionError(RuntimeError):
    """Raised when a Databricks connection or query fails."""


def _get_connection_params() -> dict[str, str]:
    """Read and validate required connection env vars, raising a clear error if any are missing."""
    hostname = os.environ.get("DATABRICKS_SERVER_HOSTNAME")
    http_path = os.environ.get("DATABRICKS_HTTP_PATH")
    token = os.environ.get("DATABRICKS_TOKEN")

    missing = [
        name
        for name, value in (
            ("DATABRICKS_SERVER_HOSTNAME", hostname),
            ("DATABRICKS_HTTP_PATH", http_path),
            ("DATABRICKS_TOKEN", token),
        )
        if not value
    ]
    if missing:
        raise DatabricksConnectionError(
            f"Missing required environment variable(s): {', '.join(missing)}. "
            "Copy .env.example to .env and fill in your Databricks credentials."
        )

    return {
        "server_hostname": hostname,  # type: ignore[dict-item]
        "http_path": http_path,  # type: ignore[dict-item]
        "access_token": token,  # type: ignore[dict-item]
    }


@contextmanager
def get_connection() -> Iterator[Connection]:
    """Yield a Databricks SQL connection, wrapping connection failures in DatabricksConnectionError."""
    params = _get_connection_params()
    try:
        connection = sql.connect(**params)
    except Exception as exc:
        raise DatabricksConnectionError(f"Failed to connect to Databricks: {exc}") from exc

    try:
        yield connection
    finally:
        connection.close()


def run_query(query: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Execute a query with named (:param) parameters and return rows as column-keyed dicts."""
    with get_connection() as connection:
        with connection.cursor() as cursor:
            try:
                cursor.execute(query, parameters)
            except Exception as exc:
                raise DatabricksConnectionError(f"Query failed: {exc}") from exc

            columns = [col[0] for col in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]


def validate_schema(schema: str) -> None:
    """Raise ValueError if schema is not one of the known lakehouse schemas."""
    if schema not in ALLOWED_SCHEMAS:
        raise ValueError(
            f"Unknown schema '{schema}'. Must be one of: {', '.join(sorted(ALLOWED_SCHEMAS))}"
        )


def validate_table(schema: str, table_name: str) -> None:
    """Raise ValueError if table_name is not a known table within schema."""
    validate_schema(schema)
    if table_name not in ALLOWED_TABLES[schema]:
        raise ValueError(
            f"Unknown table '{table_name}' in schema '{schema}'. "
            f"Must be one of: {', '.join(sorted(ALLOWED_TABLES[schema]))}"
        )
