"""Tests for approving descriptions and publishing them as column comments."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from medallion_rag.subagents import governance as gov


def approved_row(table: str = "orders", column: str = "clerk", description: str = "Clerk.") -> dict[str, str]:
    """Build one approved data_dictionary row."""
    return {"table_name": table, "column_name": column, "description": description}


def test_quote_sql_string_cannot_be_broken_out_of() -> None:
    """Quotes and backslashes in model output stay inside the literal."""
    assert gov.quote_sql_string("Clerk's id") == r"'Clerk\'s id'"
    assert gov.quote_sql_string("ends with \\") == r"'ends with \\'"
    assert gov.quote_sql_string(r"\' ; DROP TABLE x --") == r"'\\\' ; DROP TABLE x --'"


def test_format_comment_appends_lineage_only_when_known() -> None:
    """Columns without a source column keep their description unchanged."""
    assert gov.format_comment("Clerk.", []) == "Clerk."
    assert gov.format_comment("Clerk.", ["a.b.c", "a.b.d"]) == "Clerk. Derived from: a.b.c, a.b.d."


@pytest.fixture
def pending(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Fake one pending row and return the query mock to inspect approvals."""
    query = MagicMock(return_value=[approved_row()])
    monkeypatch.setattr(gov, "run_query", query)
    return query


def updates(query: MagicMock) -> list[dict[str, str]]:
    """Return the parameters of each UPDATE statement sent."""
    return [call.args[1] for call in query.call_args_list if "UPDATE" in call.args[0]]


def test_listing_pending_rows_approves_nothing(pending: MagicMock) -> None:
    """Running with no arguments is a read-only review."""
    assert gov.approve([], approve_all=False) == 0
    assert updates(pending) == []


def test_approve_selected_column(pending: MagicMock) -> None:
    """The named column moves to approved."""
    assert gov.approve(["orders.clerk"], approve_all=False) == 0
    assert updates(pending) == [{"source": "approved", "table_name": "orders", "column_name": "clerk"}]


def test_unknown_column_fails_without_approving_the_valid_ones(pending: MagicMock) -> None:
    """A typo must not leave a partial approval behind."""
    assert gov.approve(["orders.clerk", "orders.typo"], approve_all=False) == 1
    assert updates(pending) == []


def test_approve_all(pending: MagicMock) -> None:
    """--all approves every pending row."""
    assert gov.approve([], approve_all=True) == 0
    assert len(updates(pending)) == 1


@pytest.fixture
def catalog(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Fake lineage and comment writes, and return the write mock."""
    write = MagicMock()
    lineage = {("orders", "clerk"): ["1_bronze.raw_orders.o_clerk"]}
    monkeypatch.setattr(gov, "fetch_column_lineage", lambda: lineage)
    monkeypatch.setattr(gov, "write_comment", write)
    return write


def publish(
    monkeypatch: pytest.MonkeyPatch,
    rows: list[dict[str, str]],
    comments: dict[tuple[str, str], str | None],
    dry_run: bool = False,
) -> int:
    """Publish the given approved rows against the given current catalog comments."""
    monkeypatch.setattr(gov, "fetch_rows", lambda source: rows if source == gov.APPROVED else [])
    monkeypatch.setattr(gov, "fetch_current_comments", lambda: comments)
    return gov.publish_comments(dry_run=dry_run)


def test_publish_writes_description_with_lineage(catalog: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    """A column with no comment gets the description and its source."""
    assert publish(monkeypatch, [approved_row()], {("orders", "clerk"): None}) == 0
    catalog.assert_called_once_with("orders", "clerk", "Clerk. Derived from: 1_bronze.raw_orders.o_clerk.")


def test_publish_skips_comments_that_are_already_current(
    catalog: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A repeat run writes nothing, which avoids needless table metadata commits."""
    current = {("orders", "clerk"): "Clerk. Derived from: 1_bronze.raw_orders.o_clerk."}

    assert publish(monkeypatch, [approved_row()], current) == 0
    catalog.assert_not_called()


def test_publish_dry_run_writes_nothing(catalog: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    """A dry run only logs the comments."""
    assert publish(monkeypatch, [approved_row()], {("orders", "clerk"): None}, dry_run=True) == 0
    catalog.assert_not_called()


def test_publish_skips_columns_missing_from_the_catalog(
    catalog: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only columns confirmed to exist are ever placed into an ALTER statement."""
    assert publish(monkeypatch, [approved_row(column="gone`; DROP")], {("orders", "clerk"): None}) == 0
    catalog.assert_not_called()


def test_failed_write_continues_and_returns_nonzero(
    catalog: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One failure does not stop the others, but the run still reports failure."""
    catalog.side_effect = [RuntimeError("denied"), None]
    rows = [approved_row(column="clerk"), approved_row(column="status")]

    assert publish(monkeypatch, rows, {("orders", "clerk"): None, ("orders", "status"): None}) == 1
    assert catalog.call_count == 2


def test_write_comment_builds_escaped_statement(monkeypatch: pytest.MonkeyPatch) -> None:
    """The statement targets the column's own schema and escapes the comment."""
    query = MagicMock()
    monkeypatch.setattr(gov, "run_query", query)

    gov.write_comment("raw_orders", "o_clerk", "Clerk's id")

    query.assert_called_once_with(
        "ALTER TABLE `lakehouse_demo`.`1_bronze`.`raw_orders` ALTER COLUMN `o_clerk` COMMENT 'Clerk\\'s id'"
    )
