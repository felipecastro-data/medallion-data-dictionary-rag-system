"""Build the ChromaDB embedding index for lakehouse_demo.3_gold.data_dictionary.

Reads every approved data_dictionary row, embeds a "table_name.column_name: description"
string for each new or changed row with Voyage AI, and stores it in a local persistent
ChromaDB collection at ./chroma_db. Entries for rows that no longer exist or are no longer
approved are removed, so drafts don't reach the chatbot. Refuses to run while the schema-drift
check has unaccepted changes.

Usage:
    medallion-index
"""

from __future__ import annotations

import logging
import os

import chromadb
import voyageai
from dotenv import load_dotenv

from medallion_rag.hooks.detect_schema_change import run as check_schema_drift
from medallion_rag.logging_config import configure_logging
from medallion_rag.mcp_server.db import APPROVED, METADATA_TABLE, run_query
from medallion_rag.mcp_server.lineage import fetch_column_lineage

load_dotenv()

logger = logging.getLogger(__name__)

VOYAGE_MODEL = "voyage-3.5"
CHROMA_DB_PATH = "./chroma_db"
COLLECTION_NAME = "data_dictionary"


def fetch_approved_rows() -> list[dict[str, str]]:
    """Return data_dictionary rows whose description a person has approved."""
    query = f"""
        SELECT table_name, column_name, description
        FROM {METADATA_TABLE}
        WHERE description IS NOT NULL AND source = :source
        ORDER BY table_name, column_name
    """
    return run_query(query, {"source": APPROVED})


def build_embedding_input(table_name: str, column_name: str, description: str) -> str:
    """Combine table_name, column_name, and description into one embeddable string."""
    return f"{table_name}.{column_name}: {description}"


def embed_documents(client: voyageai.Client, texts: list[str]) -> list[list[float]]:
    """Embed a batch of data-dictionary entries with Voyage AI, in document mode."""
    result = client.embed(texts, model=VOYAGE_MODEL, input_type="document")
    return result.embeddings


def mark_rows_embedded() -> None:
    """Set data_dictionary.embedding_id to the ChromaDB id of indexed rows, and clear it on the rest."""
    indexed_id = """
        CASE WHEN description IS NOT NULL AND source = :source
             THEN concat(table_name, '.', column_name)
        END
    """
    run_query(
        f"""
        UPDATE {METADATA_TABLE}
        SET embedding_id = {indexed_id}
        WHERE embedding_id IS DISTINCT FROM {indexed_id}
        """,
        {"source": APPROVED},
    )


def run() -> None:
    """Embed new or changed approved rows and remove entries that no longer qualify from the index."""
    rows = fetch_approved_rows()
    if not rows:
        logger.warning(
            "No approved rows in %s, so the index wasn't changed. Review drafts with medallion-approve.",
            METADATA_TABLE,
        )
        return

    lineage = fetch_column_lineage()
    rows_by_id = {f"{row['table_name']}.{row['column_name']}": row for row in rows}
    documents = {
        entry_id: build_embedding_input(row["table_name"], row["column_name"], row["description"])
        for entry_id, row in rows_by_id.items()
    }
    metadatas = {
        entry_id: {
            "table_name": row["table_name"],
            "column_name": row["column_name"],
            "description": row["description"],
            "derived_from": ", ".join(lineage.get((row["table_name"], row["column_name"]), [])),
            "embedding_model": VOYAGE_MODEL,
        }
        for entry_id, row in rows_by_id.items()
    }

    chroma_client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
    collection = chroma_client.get_or_create_collection(name=COLLECTION_NAME)
    existing = collection.get(include=["documents", "metadatas"])
    indexed = {
        entry_id: (document, (metadata or {}).get("embedding_model"))
        for entry_id, document, metadata in zip(
            existing["ids"], existing["documents"], existing["metadatas"], strict=True
        )
    }

    # Vectors from different models can't be compared, so a model change re-embeds every row.
    changed_ids = [
        entry_id
        for entry_id, document in documents.items()
        if indexed.get(entry_id) != (document, VOYAGE_MODEL)
    ]
    stale_ids = [entry_id for entry_id in indexed if entry_id not in documents]

    if changed_ids:
        logger.info("Embedding %d new or changed row(s) with %s...", len(changed_ids), VOYAGE_MODEL)
        voyage_client = voyageai.Client(api_key=os.environ["VOYAGE_API_KEY"])
        changed_documents = [documents[entry_id] for entry_id in changed_ids]
        collection.upsert(
            ids=changed_ids,
            embeddings=embed_documents(voyage_client, changed_documents),
            documents=changed_documents,
            metadatas=[metadatas[entry_id] for entry_id in changed_ids],
        )

    # Lineage is not embedded, so it can change without a paid re-embed of the row.
    unchanged_ids = [entry_id for entry_id in documents if entry_id not in changed_ids]
    if unchanged_ids:
        collection.update(ids=unchanged_ids, metadatas=[metadatas[entry_id] for entry_id in unchanged_ids])

    if stale_ids:
        collection.delete(ids=stale_ids)

    mark_rows_embedded()
    logger.info(
        "Run complete: embedded %d row(s), removed %d, left %d unchanged.",
        len(changed_ids),
        len(stale_ids),
        len(unchanged_ids),
    )


def main() -> int:
    """CLI entry point: rebuild the index only if the schema-drift check passes."""
    configure_logging()
    if check_schema_drift() != 0:
        logger.error("Index not rebuilt because of unaccepted schema changes.")
        return 1
    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
