"""Streamlit chat UI for the Medallion Architecture data dictionary RAG assistant.

Retrieves relevant data_dictionary entries via embeddings.query.retrieve, then asks
the Anthropic API to answer the user's question using those entries as context.

Usage:
    streamlit run chatbot/app.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import streamlit as st
from anthropic import Anthropic
from dotenv import load_dotenv

# Streamlit executes this script with only its own directory on sys.path, but
# embeddings/ and mcp_server/ are sibling packages at the project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from embeddings.query import retrieve  # noqa: E402
from mcp_server.db import ALLOWED_TABLES  # noqa: E402

load_dotenv()

MODEL = "claude-haiku-4-5-20251001"
TOP_K = 5

# Confirmed during retrieval testing: the same real-world concept (e.g. order priority)
# can surface near-identical distances from its bronze and silver representations. Below
# this gap, treat the top two matches as a tie rather than trusting rank order alone.
NEAR_TIE_THRESHOLD = 0.03

LAYER_LABELS: dict[str, str] = {
    "1_bronze": "bronze, as-ingested",
    "2_silver": "silver, cleaned",
    "3_gold": "gold, aggregated",
}
LAYER_RANK: dict[str, int] = {"1_bronze": 0, "2_silver": 1, "3_gold": 2}

# Reverse of mcp_server.db.ALLOWED_TABLES: table_name -> schema, so retrieved rows
# (which only carry table_name) can be labeled with their medallion layer.
TABLE_TO_SCHEMA: dict[str, str] = {
    table_name: schema
    for schema, table_names in ALLOWED_TABLES.items()
    for table_name in table_names
}

SYSTEM_PROMPT = (
    "You are a data-dictionary assistant for a Medallion Architecture (Bronze/Silver/Gold) "
    "e-commerce lakehouse. Answer the user's question using ONLY the retrieved data "
    "dictionary entries given as context. Always reference the exact table_name.column_name "
    "for anything you mention. If the context doesn't contain a clear answer, say so instead "
    "of guessing.\n\n"
    "Format like a chat reply, not a document: plain conversational prose in short "
    "paragraphs. No markdown headers (#, ##), no bold section titles, no standalone "
    "'Summary'/'Key Insight' sections, no title line. Use a bullet list only when it "
    "genuinely helps — e.g. listing several matching columns — not as a default structure. "
    "A simple question about one column deserves a few sentences, not a multi-section "
    "writeup; let the length scale with how many distinct columns or medallion layers are "
    "actually being compared."
)


@st.cache_resource
def get_anthropic_client() -> Anthropic:
    """Build a cached Anthropic client for the Streamlit session."""
    return Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def format_context(results: list[dict[str, str | float]]) -> str:
    """Render retrieved data_dictionary entries as a bullet list for the prompt."""
    lines = []
    for r in results:
        layer = LAYER_LABELS.get(TABLE_TO_SCHEMA.get(r["table_name"], ""), "unknown layer")
        lines.append(f"- {r['table_name']}.{r['column_name']} ({layer}): {r['description']}")
    return "\n".join(lines)


def near_tie_instruction(results: list[dict[str, str | float]]) -> str | None:
    """Return an extra prompt instruction when the top-2 matches are near-tied.

    Left unhandled, the model silently answers from only the closer match and hides
    that a near-identical duplicate exists at another medallion layer.
    """
    if len(results) < 2:
        return None

    top, runner_up = results[0], results[1]
    if (runner_up["distance"] - top["distance"]) > NEAR_TIE_THRESHOLD:
        return None

    top_schema = TABLE_TO_SCHEMA.get(top["table_name"])
    runner_up_schema = TABLE_TO_SCHEMA.get(runner_up["table_name"])

    if top_schema and runner_up_schema and top_schema != runner_up_schema:
        # Gold tables are purpose-built aggregates, not simply "more refined" versions
        # of a silver/bronze column — recommending gold as the strictly better version
        # (as bronze-vs-silver does below) would be wrong here, so let the user judge.
        if "3_gold" in (top_schema, runner_up_schema):
            gold, other = (top, runner_up) if top_schema == "3_gold" else (runner_up, top)
            other_schema = TABLE_TO_SCHEMA[other["table_name"]]
            return (
                f"The top two matches are nearly tied (distance {top['distance']:.4f} vs "
                f"{runner_up['distance']:.4f}): {other['table_name']}.{other['column_name']} "
                f"({LAYER_LABELS[other_schema]}) holds the general, canonical definition of "
                f"this concept, while {gold['table_name']}.{gold['column_name']} "
                f"({LAYER_LABELS['3_gold']}) is a purpose-built aggregate scoped to that "
                "table's specific business use case — not simply a 'better' or more refined "
                "version of the same thing. Mention both, describe what each is scoped to, "
                "and let the user judge which fits their need rather than recommending one "
                "over the other."
            )

        if LAYER_RANK[top_schema] < LAYER_RANK[runner_up_schema]:
            raw, clean = top, runner_up
        else:
            raw, clean = runner_up, top
        clean_layer = LAYER_LABELS[TABLE_TO_SCHEMA[clean["table_name"]]].split(",")[0]
        return (
            f"The top two matches are nearly tied (distance {top['distance']:.4f} vs "
            f"{runner_up['distance']:.4f}) and represent the same concept at different "
            f"medallion layers: {raw['table_name']}.{raw['column_name']} "
            f"({LAYER_LABELS[TABLE_TO_SCHEMA[raw['table_name']]]}) and "
            f"{clean['table_name']}.{clean['column_name']} "
            f"({LAYER_LABELS[TABLE_TO_SCHEMA[clean['table_name']]]}). Mention both in your "
            f"answer, and note that the {clean_layer} version is the one to use for analysis."
        )

    return (
        f"The top two matches are nearly tied (distance {top['distance']:.4f} vs "
        f"{runner_up['distance']:.4f}). Mention both {top['table_name']}.{top['column_name']} "
        f"and {runner_up['table_name']}.{runner_up['column_name']} in your answer rather than "
        "only the closer one."
    )


def generate_answer(
    client: Anthropic, question: str, results: list[dict[str, str | float]]
) -> str:
    """Ask the Anthropic API to answer question using retrieved dictionary entries as context."""
    prompt_parts = [f"Data dictionary context:\n{format_context(results)}"]

    tie_note = near_tie_instruction(results)
    if tie_note:
        prompt_parts.append(f"Important: {tie_note}")

    prompt_parts.append(f"Question: {question}")

    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": "\n\n".join(prompt_parts)}],
    )
    return response.content[0].text.strip()


def render_sources(results: list[dict[str, str | float]]) -> None:
    """Render retrieved matches in an expandable section below an assistant message."""
    with st.expander(f"Sources ({len(results)} matches)"):
        for r in results:
            layer = LAYER_LABELS.get(TABLE_TO_SCHEMA.get(r["table_name"], ""), "unknown layer")
            st.markdown(
                f"**{r['table_name']}.{r['column_name']}** · {layer} · "
                f"distance {r['distance']:.4f}\n\n{r['description']}"
            )


def main() -> None:
    """Render the Streamlit chat UI."""
    st.set_page_config(page_title="Medallion Data Dictionary", page_icon="🔍")
    st.title("🔍 Medallion Data Dictionary")
    st.caption("Ask questions about the lakehouse's tables and columns.")

    if "messages" not in st.session_state:
        st.session_state.messages = []

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            if message.get("sources"):
                render_sources(message["sources"])

    question = st.chat_input("Ask about a table or column...")
    if not question:
        return

    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        with st.spinner("Searching the data dictionary..."):
            client = get_anthropic_client()
            results = retrieve(question, top_k=TOP_K)
            answer = generate_answer(client, question, results)
        st.markdown(answer)
        render_sources(results)

    st.session_state.messages.append({"role": "assistant", "content": answer, "sources": results})


if __name__ == "__main__":
    main()
