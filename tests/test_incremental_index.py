"""Tests that the index embeds only new or changed rows and removes entries for dropped rows."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import chromadb
import pytest

from medallion_rag.embeddings import build_index


def row(column_name: str, description: str) -> dict[str, str]:
    """Build one data_dictionary row for a fixed table."""
    return {"table_name": "orders", "column_name": column_name, "description": description}


@pytest.fixture
def voyage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Use a temporary ChromaDB and a fake Voyage client, and return the client for inspection."""
    client = MagicMock()
    client.embed.side_effect = lambda texts, **_: SimpleNamespace(embeddings=[[0.1, 0.2, 0.3] for _ in texts])
    monkeypatch.setattr(build_index.voyageai, "Client", lambda **_: client)
    monkeypatch.setenv("VOYAGE_API_KEY", "test-key")
    monkeypatch.setattr(build_index, "CHROMA_DB_PATH", str(tmp_path / "chroma"))
    monkeypatch.setattr(build_index, "run_query", MagicMock(return_value=[]))
    return client


def index(rows: list[dict[str, str]], monkeypatch: pytest.MonkeyPatch) -> None:
    """Run the index build as if data_dictionary currently holds these rows."""
    monkeypatch.setattr(build_index, "fetch_described_rows", lambda: rows)
    build_index.run()


def indexed_ids() -> list[str]:
    """Return the ids currently stored in the temporary collection."""
    client = chromadb.PersistentClient(path=build_index.CHROMA_DB_PATH)
    return sorted(client.get_collection(build_index.COLLECTION_NAME).get()["ids"])


def test_unchanged_rows_are_not_embedded_again(voyage: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    """A repeat run with the same rows makes no paid embedding call."""
    rows = [row("order_id", "Order key."), row("status", "Order status.")]
    index(rows, monkeypatch)
    voyage.embed.reset_mock()

    index(rows, monkeypatch)

    voyage.embed.assert_not_called()


def test_only_new_and_changed_rows_are_embedded_and_stale_rows_removed(
    voyage: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each row is treated by what happened to it since the last run."""
    index(
        [row("kept", "Same."), row("edited", "Old."), row("dropped", "Gone soon.")],
        monkeypatch,
    )
    voyage.embed.reset_mock()

    index([row("kept", "Same."), row("edited", "New."), row("added", "Brand new.")], monkeypatch)

    assert voyage.embed.call_args.args[0] == ["orders.edited: New.", "orders.added: Brand new."]
    assert indexed_ids() == ["orders.added", "orders.edited", "orders.kept"]


def test_embedding_ids_are_recorded_after_indexing(
    voyage: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """data_dictionary.embedding_id is updated so rows can be traced to their index entry."""
    index([row("order_id", "Order key.")], monkeypatch)

    assert "SET embedding_id" in build_index.run_query.call_args.args[0]


def test_empty_dictionary_leaves_the_index_alone(voyage: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    """A dictionary read that returns nothing must not wipe the existing index."""
    index([row("order_id", "Order key.")], monkeypatch)

    index([], monkeypatch)

    assert indexed_ids() == ["orders.order_id"]
