# Design notes

How the system behaves at larger sizes, what it costs, who reviews its output, and where its security limits are. Figures come from the 2026-11-06 run in [`eval/results.json`](../eval/results.json). Anything worked out from them rather than run is marked as an estimate. Decisions are recorded in the [ADRs](adr/).

## Scalability

**Incremental re-embedding is the normal path.** `medallion-index` compares each row's text and embedding model with what's in the index, and embeds only rows that differ.

| Run | What's embedded | Time | Voyage cost |
|---|---|---:|---:|
| Nothing changed | Nothing | 4.4 s | $0 |
| Full build, or a changed embedding model | All 80 rows | 8.0 s | $0.00016 |

Lineage isn't embedded, so a lineage change updates stored metadata without a new embedding.

**Limits, all estimated from the measured figures and not tested at that size**

| Limit | Where it applies | Why |
|---|---|---|
| About 300 columns | Full build on Voyage's free tier | The build sends every changed row in one request. At about 32 tokens per row, that passes the 10,000 tokens a minute allowed. |
| 1,000 columns | Full build on any tier | [Voyage allows 1,000 texts per request](https://docs.voyageai.com/docs/embeddings), and the code doesn't split requests. |
| About 26 minutes per 1,000 columns | Generation | Columns are described one at a time at 1.53 s each, before the write for each row. |
| 8 tables | Everything | `ALLOWED_TABLES` is a fixed list, so adding a table is a code change. |

**Retrieval may degrade before those limits.** The chatbot uses the top 5 matches. A concept that appears in many places can fill them: "market segment" already takes 4 of the 5. More tables or layers could push the right column out of the top 5, which the fixed question set would show. It wouldn't show a problem with questions it doesn't contain.

The index is a local folder for one machine. See [ADR 0001](adr/0001-chromadb-vs-databricks-vector-search.md).

## Cost

List prices on the measurement date: Claude Haiku 4.5 at $1 per million input tokens and $5 per million output tokens, and Voyage `voyage-3.5` at $0.06 per million tokens. Databricks usage isn't included.

| Work | Claude tokens (input / output) | Voyage tokens | Cost |
|---|---:|---:|---:|
| Generate all 80 descriptions | 37,624 / 3,246 | None | $0.054 |
| Embed all 80 descriptions | None | 2,595 | $0.00016 |
| **Full dictionary build** | **37,624 / 3,246** | **2,595** | **$0.054** |
| One question (average of 32) | 692 / 103 | 9 | $0.0012 |

Almost all of a build's cost is generation, and almost all of a question's cost is the answer. Embedding is close to free at this size. As estimates, 1,000 columns would cost about $0.67 to generate, and 1,000 questions about $1.21.

A guardrail retry adds about one generation call. The measured run had none.

## Governance

**Who reviews descriptions.** Whoever runs `medallion-approve`. There's no separate reviewer role and no record of who approved what, other than Delta table history.

```
medallion-describe -> source = auto-generated -> medallion-approve -> source = approved -> medallion-comments
```

**What approval controls.** Only approved descriptions become Unity Catalog column comments, each followed by its upstream columns.

**What approval doesn't control.** `medallion-describe` writes drafts straight into `data_dictionary`, and `medallion-index` embeds every row with a description. So **the chatbot can answer from text nobody has approved, and doesn't tell the reader**. All 80 rows are approved today, so this only applies to future drafts.

**Why review matters.** 74 of the 80 generated descriptions were corrected after generation, including claims the data didn't support. The project owner approved the first 39 corrections before they were applied. The AI assistant then rewrote 35 more against evidence from the tables and published them, and gave the owner the list to check afterwards. All 80 are marked approved, but that doesn't mean a person read each one before it was published.

**Schema changes.** A change stops `make refresh` until someone accepts it. A dropped column's row is deleted, and its text remains only in the run log and Delta history. See [ADR 0003](adr/0003-manual-schema-hook-vs-auto-rebuild.md).

**Answers.** There's no review of chatbot answers. In the measured run, 4 of 32 added a detail the dictionary doesn't support, down from 10 before the prompt was tightened, so treat answers as a starting point and check their sources.

## Security

**The token is not read-only.** The project uses one personal access token with the `sql` scope, owned by the owner of the tables. It can write, and the project needs that: it updates `data_dictionary` and sets column comments. Every read goes through the same token. Before using this on shared data, split it into a read-only identity for sampling and a separate identity limited to the dictionary and comments.

**What `ALLOWED_TABLES` does and doesn't do.** The MCP tools check schema and table names against this list before putting them in SQL, cap samples at 50 rows, and offer no way to run arbitrary SQL. It's a check inside this application, not a permission: `run_query` runs whatever the code gives it, so Unity Catalog grants are the real boundary.

**Sample values sent to LLMs**

- During generation, up to 5 values per column, taken from 5 rows per table, go to Anthropic. That includes names, phone numbers, addresses, account balances and free-text comments.
- A description can repeat sample values, as the account balance range does. Descriptions then go to Voyage when embedded, and to Anthropic whenever they're retrieved for a question.
- Nothing masks or filters these values. **This is acceptable here only because TPC-H data is synthetic.** Before pointing this at real data, add a way to exclude columns from sampling, and check the data terms of both providers.

**Untrusted text in prompts.** Free-text sample values reach the generation prompt, so they could try to steer the model. Limits on the damage:

- The output is a description that stays a draft until approved.
- Descriptions are escaped before they go into the `ALTER TABLE` statement that sets a comment, because that statement can't take parameters.
- The chatbot has no tools and no Databricks connection, so it can only produce wrong text.

The banned-phrase filter isn't a defense against this.

**Other limits**

- Secrets come only from `.env`, which is gitignored. CI and tests use no credentials.
- The Streamlit app has no login, so it's only suitable for running locally.
- `chroma_db/` holds the descriptions unencrypted on disk.
- Databricks Free Edition has no service guarantee and [can't be used commercially](https://docs.databricks.com/aws/en/getting-started/free-edition-limitations).
