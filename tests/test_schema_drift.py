"""Tests for the schema-drift check and the way it blocks re-indexing."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from medallion_rag.embeddings import build_index
from medallion_rag.hooks import detect_schema_change as drift

BASELINE = {"1_bronze.raw_orders.o_orderkey": "LONG"}
CHANGED = {"1_bronze.raw_orders.o_orderkey": "STRING", "1_bronze.raw_orders.o_new": "INT"}


@pytest.fixture
def snapshot_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the hook at a temporary snapshot so tests never touch the real baseline."""
    path = tmp_path / "schema_snapshot.json"
    monkeypatch.setattr(drift, "SNAPSHOT_PATH", path)
    return path


def run_against(columns: dict[str, str], monkeypatch: pytest.MonkeyPatch, accept: bool = False) -> int:
    """Run the drift check as if the lakehouse currently has the given columns."""
    monkeypatch.setattr(drift, "fetch_current_columns", lambda: columns)
    return drift.run(accept=accept)


def test_first_run_saves_a_baseline_and_passes(
    snapshot_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With nothing to compare against, the current schema becomes the baseline."""
    assert run_against(BASELINE, monkeypatch) == 0
    assert drift.load_snapshot()["columns"] == BASELINE


def test_unchanged_schema_passes(snapshot_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A matching schema does not block anything."""
    run_against(BASELINE, monkeypatch)

    assert run_against(BASELINE, monkeypatch) == 0


def test_drift_keeps_failing_until_accepted(
    snapshot_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Drift must not overwrite the baseline, or the next run would pass without review."""
    run_against(BASELINE, monkeypatch)

    assert run_against(CHANGED, monkeypatch) == 1
    assert run_against(CHANGED, monkeypatch) == 1
    assert drift.load_snapshot()["columns"] == BASELINE
    assert "added    1_bronze.raw_orders.o_new" in caplog.text
    assert "retyped  1_bronze.raw_orders.o_orderkey" in caplog.text

    assert run_against(CHANGED, monkeypatch, accept=True) == 0
    assert drift.load_snapshot()["columns"] == CHANGED
    assert run_against(CHANGED, monkeypatch) == 0


@pytest.mark.parametrize(("drift_exit_code", "indexed"), [(0, True), (1, False)])
def test_index_only_rebuilds_when_the_drift_check_passes(
    drift_exit_code: int, indexed: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unaccepted drift stops the rebuild before any embedding is paid for."""
    rebuild = MagicMock()
    monkeypatch.setattr(build_index, "check_schema_drift", lambda: drift_exit_code)
    monkeypatch.setattr(build_index, "run", rebuild)

    assert build_index.main() == drift_exit_code
    assert rebuild.called is indexed
