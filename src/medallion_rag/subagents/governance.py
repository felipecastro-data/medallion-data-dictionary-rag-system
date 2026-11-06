"""Human approval of generated descriptions, and publishing them as Unity Catalog column comments.

A generated description is only a draft until someone approves it, which sets its
data_dictionary.source to 'approved'. Regenerating a description resets it to 'auto-generated',
so it needs approving again. Only approved descriptions reach the chatbot's index and become
column comments, each comment followed by the upstream columns it is derived from.

Usage:
    medallion-approve                         list descriptions awaiting approval
    medallion-approve orders.clerk ...        approve specific columns
    medallion-approve --all                   approve every pending description
    medallion-comments [--dry-run]            publish approved descriptions as column comments
"""

from __future__ import annotations

import argparse
import logging

from medallion_rag.logging_config import configure_logging
from medallion_rag.mcp_server.db import APPROVED, CATALOG, METADATA_TABLE, run_query
from medallion_rag.mcp_server.lineage import fetch_column_lineage
from medallion_rag.subagents.generate_descriptions import TABLE_TO_SCHEMA

logger = logging.getLogger(__name__)

GENERATED = "auto-generated"


def quote_sql_string(value: str) -> str:
    """Quote text as a SQL string literal, since ALTER TABLE ... COMMENT cannot take parameters.

    Descriptions are model output, so a quote or backslash in one must not be able to end the
    literal and change the statement.
    """
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


def format_comment(description: str, sources: list[str]) -> str:
    """Build a column comment from a description and its upstream columns."""
    if not sources:
        return description
    return f"{description} Derived from: {', '.join(sources)}."


def fetch_rows(source: str) -> list[dict[str, str]]:
    """Return described rows with the given status, either GENERATED or APPROVED."""
    return run_query(
        f"""
        SELECT table_name, column_name, description
        FROM {METADATA_TABLE}
        WHERE description IS NOT NULL AND source = :source
        ORDER BY table_name, column_name
        """,
        {"source": source},
    )


def approve(column_ids: list[str], approve_all: bool) -> int:
    """List pending rows, or approve them. Returns 1, changing nothing, if any id is not pending."""
    pending_by_id = {f"{row['table_name']}.{row['column_name']}": row for row in fetch_rows(GENERATED)}

    if approve_all:
        selected = list(pending_by_id)
    elif column_ids:
        unknown = sorted(set(column_ids) - set(pending_by_id))
        if unknown:
            logger.error("Not awaiting approval: %s", ", ".join(unknown))
            return 1
        selected = column_ids
    else:
        for column_id, row in pending_by_id.items():
            logger.info("%s: %s", column_id, row["description"])
        logger.info("%d description(s) awaiting approval.", len(pending_by_id))
        return 0

    for column_id in selected:
        row = pending_by_id[column_id]
        run_query(
            f"""
            UPDATE {METADATA_TABLE}
            SET source = :source
            WHERE table_name = :table_name AND column_name = :column_name
            """,
            {"source": APPROVED, "table_name": row["table_name"], "column_name": row["column_name"]},
        )
    logger.info("Run complete: approved %d description(s).", len(selected))
    return 0


def fetch_current_comments() -> dict[tuple[str, str], str | None]:
    """Return {(table, column): comment} for every column that exists in a tracked table."""
    rows = run_query(
        f"""
        SELECT table_name, column_name, comment
        FROM {CATALOG}.information_schema.columns
        WHERE table_catalog = :catalog AND table_schema IN ('1_bronze', '2_silver', '3_gold')
        """,
        {"catalog": CATALOG},
    )
    return {
        (row["table_name"], row["column_name"]): row["comment"]
        for row in rows
        if row["table_name"] in TABLE_TO_SCHEMA
    }


def write_comment(table_name: str, column_name: str, comment: str) -> None:
    """Set one column's comment. Table and column must already be known to exist in the catalog."""
    schema = TABLE_TO_SCHEMA[table_name]
    run_query(
        f"ALTER TABLE `{CATALOG}`.`{schema}`.`{table_name}` "
        f"ALTER COLUMN `{column_name}` COMMENT {quote_sql_string(comment)}"
    )


def publish_comments(dry_run: bool) -> int:
    """Write approved descriptions as column comments, skipping any already up to date.

    Returns 1 if any write failed.
    """
    lineage = fetch_column_lineage()
    current_comments = fetch_current_comments()

    written = unchanged = missing = failed = 0
    for row in fetch_rows(APPROVED):
        key = (row["table_name"], row["column_name"])
        if key not in current_comments:
            logger.warning("Skipping %s.%s: column not found in the catalog.", *key)
            missing += 1
            continue

        comment = format_comment(row["description"], lineage.get(key, []))
        if current_comments[key] == comment:
            unchanged += 1
            continue

        logger.info("%s.%s: %s", *key, comment)
        if dry_run:
            written += 1
            continue
        try:
            write_comment(*key, comment)
            written += 1
        except Exception as exc:
            logger.error("%s.%s: failed to write comment: %s", *key, exc)
            failed += 1

    logger.info(
        "Run complete: %s %d comment(s), %d unchanged, %d missing, %d failed, %d awaiting approval.",
        "would write" if dry_run else "wrote",
        written,
        unchanged,
        missing,
        failed,
        len(fetch_rows(GENERATED)),
    )
    return 1 if failed else 0


def approve_main() -> int:
    """CLI entry point for reviewing and approving descriptions."""
    parser = argparse.ArgumentParser(description="List or approve generated descriptions.")
    parser.add_argument("columns", nargs="*", help="Columns to approve, as table.column.")
    parser.add_argument("--all", action="store_true", help="Approve every pending description.")
    args = parser.parse_args()
    configure_logging()
    return approve(args.columns, args.all)


def comments_main() -> int:
    """CLI entry point for publishing approved descriptions as column comments."""
    parser = argparse.ArgumentParser(description="Publish approved descriptions as column comments.")
    parser.add_argument("--dry-run", action="store_true", help="Log comments without writing them.")
    args = parser.parse_args()
    configure_logging()
    return publish_comments(dry_run=args.dry_run)
