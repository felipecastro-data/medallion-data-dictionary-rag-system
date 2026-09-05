"""Semantic search over the data_dictionary ChromaDB index built by build_index.py.

Usage:
    from embeddings.query import retrieve
    retrieve("what column has customer revenue")
"""

from __future__ import annotations

import os

import chromadb
import voyageai
from dotenv import load_dotenv

from embeddings.build_index import CHROMA_DB_PATH, COLLECTION_NAME, VOYAGE_MODEL

load_dotenv()


def retrieve(question: str, top_k: int = 5) -> list[dict[str, str | float]]:
    """Return the data_dictionary entries most semantically similar to a question.

    Args:
        question: Natural-language question about the lakehouse's tables/columns.
        top_k: Maximum number of matches to return.

    Returns:
        Records with table_name, column_name, description, and distance (lower is a
        closer match), ordered by increasing distance.
    """
    voyage_client = voyageai.Client(api_key=os.environ["VOYAGE_API_KEY"])
    query_embedding = voyage_client.embed(
        [question], model=VOYAGE_MODEL, input_type="query"
    ).embeddings[0]

    chroma_client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
    collection = chroma_client.get_or_create_collection(name=COLLECTION_NAME)
    results = collection.query(query_embeddings=[query_embedding], n_results=top_k)

    metadatas = results["metadatas"][0]
    distances = results["distances"][0]
    return [
        {
            "table_name": metadata["table_name"],
            "column_name": metadata["column_name"],
            "description": metadata["description"],
            "distance": distance,
        }
        for metadata, distance in zip(metadatas, distances)
    ]
