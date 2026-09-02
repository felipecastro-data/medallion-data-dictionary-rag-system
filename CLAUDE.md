# Medallion Architecture Data Dictionary RAG

## Project Purpose

A retrieval-augmented generation (RAG) system that lets users ask natural-language
questions about a Medallion Architecture (Bronze/Silver/Gold) data lakehouse and get
accurate answers about table structure, column meaning, and data lineage. The system
auto-generates and maintains a data dictionary for the lakehouse, embeds it for
semantic search, and exposes it through a chatbot interface.

This is a public portfolio project — code quality, clarity, and documentation matter
as much as working functionality.

## Data Source

- **Platform**: Databricks Free Edition
- **Catalog**: `lakehouse_demo`
- **Schemas**:
  - `1_bronze` — raw ingested data
  - `2_silver` — cleaned/conformed data
  - `3_gold` — business-level aggregates
- **Tables** (8 total, TPC-H-derived):
  - Bronze: `raw_customers`, `raw_orders`, `raw_lineitem`
  - Silver: `customers`, `orders`, `order_items`
  - Gold: `daily_sales_summary`, `customer_ltv`
- **Metadata table**: `lakehouse_demo.3_gold.data_dictionary`
  - Columns: `table_name`, `column_name`, `description`, `source`, `generated_at`, `embedding_id`
  - Pre-populated with `table_name`/`column_name` for all 8 tables; other columns are
    populated by this system.

### Scale caution

Bronze and Silver tables are TPC-H **production scale**. Every query against
`1_bronze` or `2_silver` tables **must** include a `LIMIT` clause. Never run a full
table scan against these schemas. Gold tables are pre-aggregated and safe to query
without a `LIMIT`, but prefer one anyway when sampling.

## Architecture

```
Databricks (lakehouse_demo)
        |
        v
  MCP Server  ---- exposes schema/metadata + query tools over MCP
        |
        v
   Subagent   ---- reads table/column metadata via MCP, drafts descriptions
        |
        v
  Embeddings  ---- embeds dictionary entries (description text) into ChromaDB
        |
        v
   Chatbot    ---- Streamlit UI; RAG over ChromaDB, answers via Anthropic API
```

- **`/mcp_server`** — MCP server exposing Databricks schema inspection and
  guarded (LIMIT-enforced) query tools.
- **`/subagents`** — Agent(s) that use the MCP server to inspect tables/columns and
  generate human-readable descriptions to populate `data_dictionary`.
- **`/embeddings`** — Embedding pipeline: reads `data_dictionary` rows, generates
  embeddings, stores/indexes them in ChromaDB.
- **`/chatbot`** — Streamlit chat app; retrieves relevant dictionary entries from
  ChromaDB and answers user questions via the Anthropic API.
- **`/hooks`** — Automation hooks (e.g., regenerate embeddings when the dictionary
  changes).
- **`/docs`** — Supplementary documentation (architecture notes, setup guides).

## Coding Conventions

- **Python**: 3.10+
- **Type hints**: required on all function signatures (parameters and return types).
- **Docstrings**: required on all public functions, classes, and modules. Use concise
  docstrings that explain *why*/*what*, not line-by-line restatement of the code.
- **Secrets**: never hardcode credentials or API keys. All configuration
  (`DATABRICKS_SERVER_HOSTNAME`, `DATABRICKS_HTTP_PATH`, `DATABRICKS_TOKEN`,
  `ANTHROPIC_API_KEY`) is loaded from environment variables via `.env`
  (see `.env.example`). `.env` is gitignored and must never be committed.
- **Bronze/Silver queries**: always include `LIMIT` — see Scale caution above.
- **No premature abstraction**: keep modules focused; avoid speculative
  configurability or frameworks beyond what the task requires.
