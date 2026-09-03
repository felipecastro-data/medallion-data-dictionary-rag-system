"""Subagent that populates lakehouse_demo.3_gold.data_dictionary with column descriptions.

For each (table_name, column_name) row in data_dictionary that has no description yet,
this gathers schema and sample-value context for that column via the MCP server's tools,
asks the Anthropic API for a concise business-relevant description, and writes it back.

Usage:
    python -m subagents.generate_descriptions [--dry-run]

--dry-run prints the generated descriptions without writing to Databricks, so output
quality can be reviewed before committing writes.
"""

from __future__ import annotations

import argparse
import os
from collections import defaultdict
from datetime import datetime, timezone

from anthropic import Anthropic
from dotenv import load_dotenv

from mcp_server.db import ALLOWED_TABLES, METADATA_TABLE, run_query
from mcp_server.server import get_sample_rows, get_schema

load_dotenv()

MODEL = "claude-haiku-4-5-20251001"
MAX_SAMPLE_VALUES = 5
SAMPLE_ROWS_LIMIT = 5

# Reverse of mcp_server.db.ALLOWED_TABLES: table_name -> schema. data_dictionary rows only
# carry table_name, but get_schema/get_sample_rows require the owning schema too.
TABLE_TO_SCHEMA: dict[str, str] = {
    table_name: schema
    for schema, table_names in ALLOWED_TABLES.items()
    for table_name in table_names
}


def fetch_pending_rows() -> list[dict[str, str]]:
    """Return data_dictionary rows that still need a description, grouped implicitly by order."""
    query = f"""
        SELECT table_name, column_name
        FROM {METADATA_TABLE}
        WHERE description IS NULL
        ORDER BY table_name, column_name
    """
    return run_query(query)


def group_by_table(rows: list[dict[str, str]]) -> dict[str, list[str]]:
    """Group (table_name, column_name) rows into {table_name: [column_name, ...]}."""
    grouped: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        grouped[row["table_name"]].append(row["column_name"])
    return grouped


def sample_values_for_column(
    sample_rows: list[dict[str, object]], column_name: str, max_values: int = MAX_SAMPLE_VALUES
) -> list[str]:
    """Pull up to max_values distinct, non-null string values for one column from sample rows."""
    values: list[str] = []
    seen: set[str] = set()
    for row in sample_rows:
        value = row.get(column_name)
        if value is None:
            continue
        text = str(value)
        if text in seen:
            continue
        seen.add(text)
        values.append(text)
        if len(values) >= max_values:
            break
    return values


_BRONZE_GUIDANCE = (
    "This is a BRONZE (raw ingestion) layer column. Describe it as raw data, as-ingested "
    "from the source system. Do NOT assert data quality guarantees such as uniqueness, "
    "primary-key status, deduplication, or clean joinability — those have not been "
    "established yet at this layer."
)
_SILVER_GOLD_GUIDANCE = (
    "This is a {layer} (cleaned/transformed) layer column. The data has already been "
    "cleaned and conformed, so it is fine to describe relational roles (e.g. primary key, "
    "foreign key, used to join) where appropriate."
)


def build_prompt(
    *, table_name: str, schema: str, column_name: str, data_type: str, sample_values: list[str]
) -> str:
    """Build the description-generation prompt for a single column."""
    samples = ", ".join(sample_values) if sample_values else "(no non-null sample values found)"
    layer_guidance = (
        _BRONZE_GUIDANCE
        if schema == "1_bronze"
        else _SILVER_GOLD_GUIDANCE.format(layer="SILVER" if schema == "2_silver" else "GOLD")
    )
    return (
        "You are writing a data dictionary entry for a column in a lakehouse table.\n\n"
        f"Table: {table_name} (schema: {schema}, part of an e-commerce lakehouse)\n"
        f"Column: {column_name}\n"
        f"Data type: {data_type}\n"
        f"Sample values: {samples}\n\n"
        "Write a concise, business-relevant description of this column in 1-2 sentences, "
        "for a data analyst browsing the data dictionary. Describe what the data represents "
        "and how it is used, not just its type.\n\n"
        "Rules:\n"
        f"- {layer_guidance}\n"
        "- Only describe what is directly observable from the column name, data type, and "
        "the sample values above. Do not infer or assert details (such as currency, units "
        "of measure, or encoding schemes) that are not evidenced by the sample values.\n"
        "- Do not speculate about encoding, hashing, formatting, or storage schemes (e.g. "
        "'encoded', 'hashed', 'may require decoding') unless the sample values themselves "
        "visibly show that pattern (e.g. an actual hex or base64-looking string). If a "
        "sample value is just an ordinary string, describe it as what it plainly appears "
        "to be — do not invent a transformation it might need.\n"
        "- Do not add generic caveats like 'may contain data quality issues', 'may require "
        "validation', or 'typical of bronze-layer data' unless the sample values shown "
        "directly demonstrate that specific problem (e.g. an actual null, an actual "
        "malformed value). A caveat must point to something visible in the samples, not "
        "a boilerplate warning about the layer in general.\n"
        "- Write in pure business language. Do not name or reference the underlying "
        "dataset, benchmark, or its technical origin.\n\n"
        "Respond with only the description text, no preamble."
    )


def generate_description(
    client: Anthropic,
    *,
    table_name: str,
    schema: str,
    column_name: str,
    data_type: str,
    sample_values: list[str],
) -> str:
    """Call the Anthropic API to generate a 1-2 sentence description for one column."""
    prompt = build_prompt(
        table_name=table_name,
        schema=schema,
        column_name=column_name,
        data_type=data_type,
        sample_values=sample_values,
    )
    response = client.messages.create(
        model=MODEL,
        max_tokens=150,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text.strip()


# Phrases observed (via manual review) where the model speculates about format, encoding,
# or data-quality issues on bronze STRING/VARCHAR columns without evidence in the sample
# values. Prompt wording alone did not eliminate this, so it is enforced programmatically.
BRONZE_STRING_BANNED_PHRASES = [
    "encoded",
    "compressed",
    "decoding",
    "hashed",
    "may capture",
    "rather than",
    "truncated",
    "partial or fragmented",
    "may vary widely",
    "may relate to",
    "typical of bronze-layer",
]


def find_banned_phrase(description: str) -> str | None:
    """Return the first banned phrase found in description (case-insensitive), or None."""
    lowered = description.lower()
    for phrase in BRONZE_STRING_BANNED_PHRASES:
        if phrase in lowered:
            return phrase
    return None


def is_bronze_string_column(schema: str, data_type: str) -> bool:
    """True for bronze-layer STRING/VARCHAR columns, where speculative-format fabrication was observed."""
    return schema == "1_bronze" and ("STRING" in data_type.upper() or "VARCHAR" in data_type.upper())


def build_corrective_prompt(
    *,
    table_name: str,
    schema: str,
    column_name: str,
    data_type: str,
    sample_values: list[str],
    previous_description: str,
    banned_phrase: str,
) -> str:
    """Build a follow-up prompt asking the model to rewrite a description that tripped the guardrail."""
    base_prompt = build_prompt(
        table_name=table_name,
        schema=schema,
        column_name=column_name,
        data_type=data_type,
        sample_values=sample_values,
    )
    return (
        f"{base_prompt}\n\n"
        f'Your previous description was: "{previous_description}"\n'
        f'That description used a forbidden phrase ("{banned_phrase}") describing '
        "format, encoding, or data-quality speculation not visible in the sample values. "
        "Rewrite the description using only what the literal sample values shown above "
        "actually demonstrate."
    )


def generate_description_with_guardrail(
    client: Anthropic,
    *,
    table_name: str,
    schema: str,
    column_name: str,
    data_type: str,
    sample_values: list[str],
) -> str:
    """Generate a description, retrying once for bronze STRING columns that trip the banned-phrase filter."""
    description = generate_description(
        client,
        table_name=table_name,
        schema=schema,
        column_name=column_name,
        data_type=data_type,
        sample_values=sample_values,
    )

    if not is_bronze_string_column(schema, data_type):
        return description

    banned_phrase = find_banned_phrase(description)
    if banned_phrase is None:
        return description

    print(f"  {column_name:<24} [guardrail] tripped on '{banned_phrase}', retrying once...")
    corrective_prompt = build_corrective_prompt(
        table_name=table_name,
        schema=schema,
        column_name=column_name,
        data_type=data_type,
        sample_values=sample_values,
        previous_description=description,
        banned_phrase=banned_phrase,
    )
    response = client.messages.create(
        model=MODEL,
        max_tokens=150,
        messages=[{"role": "user", "content": corrective_prompt}],
    )
    retried_description = response.content[0].text.strip()

    still_banned = find_banned_phrase(retried_description)
    if still_banned is not None:
        print(
            f"  {column_name:<24} [guardrail] WARNING: retry still contains '{still_banned}' "
            "— flagging for manual review."
        )

    return retried_description


def write_description(table_name: str, column_name: str, description: str) -> None:
    """UPDATE one data_dictionary row with its generated description and metadata."""
    query = f"""
        UPDATE {METADATA_TABLE}
        SET description = :description,
            source = 'auto-generated',
            generated_at = :generated_at
        WHERE table_name = :table_name AND column_name = :column_name
    """
    run_query(
        query,
        {
            "description": description,
            "generated_at": datetime.now(timezone.utc),
            "table_name": table_name,
            "column_name": column_name,
        },
    )


def run(dry_run: bool) -> None:
    """Generate (and optionally write) descriptions for every pending data_dictionary row."""
    pending_rows = fetch_pending_rows()
    if not pending_rows:
        print("No rows need descriptions (data_dictionary.description already populated).")
        return

    grouped = group_by_table(pending_rows)
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    total_generated = 0
    total_written = 0

    for table_name, column_names in grouped.items():
        schema = TABLE_TO_SCHEMA.get(table_name)
        if schema is None:
            print(f"  SKIPPING '{table_name}': not a recognized lakehouse table, skipping.")
            continue

        print(f"\n=== {table_name} ({schema}) ===")

        # One MCP call each for schema and sample rows per table, not per column.
        columns_schema = {row["column_name"]: row["data_type"] for row in get_schema(table_name, schema)}
        sample_rows = get_sample_rows(table_name, schema, limit=SAMPLE_ROWS_LIMIT)

        for column_name in column_names:
            data_type = columns_schema.get(column_name, "UNKNOWN")
            sample_values = sample_values_for_column(sample_rows, column_name)

            try:
                description = generate_description_with_guardrail(
                    client,
                    table_name=table_name,
                    schema=schema,
                    column_name=column_name,
                    data_type=data_type,
                    sample_values=sample_values,
                )
            except Exception as exc:
                print(f"  {column_name:<24} FAILED to generate description: {exc}")
                continue

            total_generated += 1
            print(f"  {column_name:<24} {description}")

            if not dry_run:
                try:
                    write_description(table_name, column_name, description)
                    total_written += 1
                except Exception as exc:
                    print(f"  {column_name:<24} FAILED to write to Databricks: {exc}")

    print(f"\nGenerated {total_generated} description(s).")
    if dry_run:
        print("Dry run: nothing was written to Databricks. Re-run without --dry-run to commit.")
    else:
        print(f"Wrote {total_written} description(s) to {METADATA_TABLE}.")


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print generated descriptions without writing them to Databricks.",
    )
    args = parser.parse_args()
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
