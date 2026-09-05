"""Build the ChromaDB embedding index for lakehouse_demo.3_gold.data_dictionary.

Reads every data_dictionary row that has a description, embeds a
"table_name.column_name: description" string for each with Voyage AI, and upserts
the embeddings into a local persistent ChromaDB collection at ./chroma_db.

Usage:
    python -m embeddings.build_index
"""

from __future__ import annotations

import os

import chromadb
import voyageai
from dotenv import load_dotenv

from mcp_server.db import METADATA_TABLE, run_query

load_dotenv()

VOYAGE_MODEL = "voyage-3.5"
CHROMA_DB_PATH = "./chroma_db"
COLLECTION_NAME = "data_dictionary"


def fetch_described_rows() -> list[dict[str, str]]:
    """Return data_dictionary rows that have a non-null description."""
    query = f"""
        SELECT table_name, column_name, description
        FROM {METADATA_TABLE}
        WHERE description IS NOT NULL
        ORDER BY table_name, column_name
    """
    return run_query(query)


def build_embedding_input(table_name: str, column_name: str, description: str) -> str:
    """Combine table_name, column_name, and description into one embeddable string."""
    return f"{table_name}.{column_name}: {description}"


def embed_documents(client: voyageai.Client, texts: list[str]) -> list[list[float]]:
    """Embed a batch of data-dictionary entries with Voyage AI, in document mode."""
    result = client.embed(texts, model=VOYAGE_MODEL, input_type="document")
    return result.embeddings


def run() -> None:
    """Fetch described rows, embed them, and upsert into the local ChromaDB collection."""
    rows = fetch_described_rows()
    if not rows:
        print(f"No rows with a description found in {METADATA_TABLE}.")
        return

    ids = [f"{row['table_name']}.{row['column_name']}" for row in rows]
    documents = [
        build_embedding_input(row["table_name"], row["column_name"], row["description"])
        for row in rows
    ]
    metadatas = [
        {
            "table_name": row["table_name"],
            "column_name": row["column_name"],
            "description": row["description"],
        }
        for row in rows
    ]

    print(f"Embedding {len(documents)} data_dictionary row(s) with {VOYAGE_MODEL}...")
    voyage_client = voyageai.Client(api_key=os.environ["VOYAGE_API_KEY"])
    embeddings = embed_documents(voyage_client, documents)

    chroma_client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
    collection = chroma_client.get_or_create_collection(name=COLLECTION_NAME)
    collection.upsert(ids=ids, embeddings=embeddings, documents=documents, metadatas=metadatas)

    print(f"Upserted {len(ids)} embedding(s) into '{COLLECTION_NAME}' at {CHROMA_DB_PATH}.")


if __name__ == "__main__":
    run()
