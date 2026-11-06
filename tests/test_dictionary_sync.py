"""Tests for keeping data_dictionary rows in step with the lakehouse schema."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from medallion_rag.subagents import generate_descriptions as gd


def fake_lakehouse(
    monkeypatch: pytest.MonkeyPatch, columns: list[str], dictionary: list[tuple[str, str]]
) -> MagicMock:
    """Fake the schema and dictionary reads, and return the query mock to inspect writes."""
    monkeypatch.setattr(gd, "fetch_current_columns", lambda: dict.fromkeys(columns, "STRING"))
    query = MagicMock(
        return_value=[
            {"table_name": table, "column_name": column, "description": f"About {column}."}
            for table, column in dictionary
        ]
    )
    monkeypatch.setattr(gd, "run_query", query)
    return query


def writes(query: MagicMock) -> list[tuple[str, dict[str, str]]]:
    """Return (SQL verb, parameters) for each statement after the initial dictionary read."""
    return [(call.args[0].split()[0], call.args[1]) for call in query.call_args_list[1:]]


def test_new_columns_are_inserted_and_dropped_columns_deleted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Existing rows keep their descriptions because they are not touched."""
    query = fake_lakehouse(
        monkeypatch,
        ["2_silver.orders.order_id", "2_silver.orders.added"],
        [("orders", "order_id"), ("orders", "dropped")],
    )

    gd.sync_dictionary_rows(dry_run=False)

    assert writes(query) == [
        ("INSERT", {"table_name": "orders", "column_name": "added"}),
        ("DELETE", {"table_name": "orders", "column_name": "dropped"}),
    ]


def test_deleted_description_is_kept_in_the_log(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A dropped column takes its description with it, so the log records the text."""
    fake_lakehouse(monkeypatch, ["2_silver.orders.order_id"], [("orders", "dropped")])

    with caplog.at_level("INFO", logger=gd.__name__):
        gd.sync_dictionary_rows(dry_run=False)

    assert "orders.dropped (description: About dropped.)" in caplog.text


def test_dry_run_writes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A dry run only reports what would change."""
    query = fake_lakehouse(monkeypatch, ["2_silver.orders.added"], [("orders", "dropped")])

    gd.sync_dictionary_rows(dry_run=True)

    assert writes(query) == []


def test_rows_for_untracked_tables_are_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only tables in ALLOWED_TABLES are compared, so other rows are never deleted."""
    query = fake_lakehouse(
        monkeypatch, ["2_silver.orders.order_id"], [("orders", "order_id"), ("data_dictionary", "table_name")]
    )

    gd.sync_dictionary_rows(dry_run=False)

    assert writes(query) == []


def test_empty_schema_result_refuses_to_delete_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty schema read points to a problem, not to every column being dropped."""
    query = fake_lakehouse(monkeypatch, [], [("orders", "order_id")])

    with pytest.raises(RuntimeError, match="refusing to sync"):
        gd.sync_dictionary_rows(dry_run=False)

    query.assert_not_called()


def test_failed_description_makes_the_run_exit_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failure must stop `make refresh` before the index is rebuilt."""
    monkeypatch.setattr(gd, "sync_dictionary_rows", lambda dry_run: None)
    monkeypatch.setattr(gd, "fetch_pending_rows", lambda: [{"table_name": "orders", "column_name": "status"}])
    monkeypatch.setattr(gd, "get_schema", lambda *_: [])
    monkeypatch.setattr(gd, "get_sample_rows", lambda *_, **__: [])
    monkeypatch.setattr(gd, "Anthropic", MagicMock())
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    failing = MagicMock(side_effect=RuntimeError("boom"))
    monkeypatch.setattr(gd, "generate_description_with_guardrail", failing)

    assert gd.run(dry_run=True) == 1


def run_cli(
    monkeypatch: pytest.MonkeyPatch, drift_exit_code: int, *args: str
) -> tuple[int, MagicMock, MagicMock]:
    """Run medallion-describe's entry point with fakes, returning its exit code, drift check and run."""
    drift_check = MagicMock(return_value=drift_exit_code)
    describe = MagicMock(return_value=0)
    monkeypatch.setattr(gd, "check_schema_drift", drift_check)
    monkeypatch.setattr(gd, "run", describe)
    monkeypatch.setattr(gd, "configure_logging", lambda: None)
    monkeypatch.setattr("sys.argv", ["medallion-describe", *args])
    return gd.main(), drift_check, describe


def test_unaccepted_schema_change_stops_before_any_row_is_touched(monkeypatch: pytest.MonkeyPatch) -> None:
    """Syncing deletes dropped columns' rows, so it must wait until the change is accepted."""
    exit_code, _, describe = run_cli(monkeypatch, 1)

    assert exit_code == 1
    describe.assert_not_called()


def test_accepted_schema_lets_the_run_continue(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no drift, the command behaves as before."""
    exit_code, _, describe = run_cli(monkeypatch, 0)

    assert exit_code == 0
    describe.assert_called_once_with(dry_run=False)


def test_dry_run_skips_the_schema_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """A dry run writes nothing, so it can preview a change that hasn't been accepted yet."""
    exit_code, drift_check, describe = run_cli(monkeypatch, 1, "--dry-run")

    assert exit_code == 0
    drift_check.assert_not_called()
    describe.assert_called_once_with(dry_run=True)
