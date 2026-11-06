# ADR 0001: ChromaDB instead of Databricks Vector Search

- **Status:** Accepted
- **Recorded:** 2026-11-06, after the choice was made and in use

## Context

The chatbot needs semantic search over the data dictionary. The index is small: 80 entries, one per column, about 32 tokens each. It changes only when a description changes.

The lakehouse runs on Databricks Free Edition, which [allows one AI Search endpoint with one search unit, and doesn't support Direct Vector Access](https://docs.databricks.com/aws/en/getting-started/free-edition-limitations). AI Search is the current name for Vector Search. The project's access token has only the `sql` scope.

## Options

1. **ChromaDB, stored locally.** Embed with Voyage AI, and keep the vectors in a folder on the machine that runs the chatbot.
2. **Databricks Vector Search.** Keep the index next to the data, synced from a Delta table.

## Decision

Use ChromaDB (option 1). Nothing else needs to run, it uses the project's one embedding model directly, and the chatbot never needs a connection to Databricks to answer a question.

## Trade-offs

**Cost.** ChromaDB adds no service cost. A full rebuild took 8.0 s and $0.00016 of Voyage tokens, so losing the index costs very little. Vector Search would use the Free Edition's only endpoint, and its cost on a paid workspace wasn't checked.

**Accuracy.** Vector Search wasn't tested, so there's no comparison to report. With ChromaDB, the expected column ranked first on 22 of 26 scored questions and was in the top 5 on all 26. Those figures depend mostly on the embedding model and description text, which Vector Search could also use. With only 80 entries, the choice of search engine is unlikely to change them much, but that's a judgement rather than a measurement.

**Latency.** Median retrieval was 0.21 s, including the Voyage call, against a median of 1.86 s for the whole answer. Search isn't the slow step.

**Costs of this choice**

- The index belongs to one machine. It isn't shared, backed up or access-controlled, and anyone else must rebuild their own.
- It's a second copy of the descriptions, so it can fall out of step with `data_dictionary`. `medallion-index` compares text and embedding model to catch that, and `embedding_id` records what was indexed.
- This wouldn't suit many concurrent users or a much larger index. Neither has been tested.

## When to revisit

Move to a managed index if several people or services need the same index, or if the dictionary grows well beyond a single team's lakehouse. See the [design doc](../design.md) for the limits.
