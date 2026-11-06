"""Measure the numbers the README states: accuracy, latency, build time and cost.

Writes eval/results.json. Nothing here changes the lakehouse or the live index: descriptions are
generated but not stored, and the index is built in a temporary folder with the write that marks
rows as embedded switched off. Answer quality isn't scored here; a person grades each answer in
eval/answer_grades.json.

Usage:
    medallion-eval
"""

from __future__ import annotations

import json
import logging
import os
import statistics
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import voyageai
from anthropic import Anthropic
from dotenv import load_dotenv

from medallion_rag.chatbot.app import MODEL as CHAT_MODEL
from medallion_rag.chatbot.app import TOP_K, generate_answer
from medallion_rag.embeddings import build_index
from medallion_rag.embeddings.build_index import VOYAGE_MODEL
from medallion_rag.embeddings.query import retrieve
from medallion_rag.logging_config import configure_logging
from medallion_rag.mcp_server.db import ALLOWED_TABLES
from medallion_rag.mcp_server.server import get_sample_rows, get_schema
from medallion_rag.subagents.generate_descriptions import (
    MODEL as DESCRIPTION_MODEL,
)
from medallion_rag.subagents.generate_descriptions import (
    SAMPLE_ROWS_LIMIT,
    generate_description_with_guardrail,
    sample_values_for_column,
)

load_dotenv()

logger = logging.getLogger(__name__)

QUESTIONS_PATH = Path("eval/questions.json")
RESULTS_PATH = Path("eval/results.json")
GRADES_PATH = Path("eval/answer_grades.json")

# List prices when these results were measured, in US dollars per million tokens.
HAIKU_INPUT_PRICE = 1.00
HAIKU_OUTPUT_PRICE = 5.00
VOYAGE_PRICE = 0.06

# Voyage's free tier allows 3 requests a minute. Spacing the calls keeps its retries, which
# would add up to a minute of waiting, out of the latency figures.
VOYAGE_SPACING_SECONDS = 21


class RecordingClient:
    """Anthropic client stand-in that keeps the token usage of every call made through it."""

    def __init__(self, client: Anthropic) -> None:
        self._client = client
        self.messages = self
        self.calls: list[tuple[int, int]] = []

    def create(self, **kwargs: Any) -> Any:
        """Forward to messages.create and record the input and output tokens."""
        response = self._client.messages.create(**kwargs)
        self.calls.append((response.usage.input_tokens, response.usage.output_tokens))
        return response


@contextmanager
def record_voyage_tokens() -> Iterator[list[int]]:
    """Collect the billed tokens of every Voyage embed call made inside the block."""
    original_embed = voyageai.Client.embed
    tokens: list[int] = []

    def embed(self: voyageai.Client, *args: Any, **kwargs: Any) -> Any:
        result = original_embed(self, *args, **kwargs)
        tokens.append(result.total_tokens)
        return result

    with patch.object(voyageai.Client, "embed", embed):
        yield tokens


def score_retrieval(
    expected: list[str], match: str, retrieved: list[str]
) -> tuple[bool | None, bool | None]:
    """Return (hit at 1, hit in top results) for one question, or (None, None) if nothing is expected.

    With match "any", one expected column in the results is a hit. With match "all", every expected
    column must be in the results, though first place only needs one of them.
    """
    if match == "none":
        return None, None
    hit_at_1 = bool(retrieved) and retrieved[0] in expected
    found = [column in retrieved for column in expected]
    return hit_at_1, all(found) if match == "all" else any(found)


def summarize_questions(questions: list[dict[str, Any]]) -> dict[str, Any]:
    """Return hit rates per category and overall, plus median and slowest answer latency."""

    def rates(rows: list[dict[str, Any]]) -> dict[str, Any]:
        scored = [row for row in rows if row["hit_at_1"] is not None]
        return {
            "questions": len(scored),
            "hit_at_1": sum(row["hit_at_1"] for row in scored),
            "hit_at_top_k": sum(row["hit_at_top_k"] for row in scored),
        }

    categories = sorted({row["category"] for row in questions})
    latencies = [row["retrieve_seconds"] + row["answer_seconds"] for row in questions]
    return {
        "overall": rates(questions),
        "by_category": {
            category: rates([row for row in questions if row["category"] == category])
            for category in categories
        },
        "median_latency_seconds": round(statistics.median(latencies), 2),
        "max_latency_seconds": round(max(latencies), 2),
    }


def anthropic_cost(calls: list[tuple[int, int]]) -> float:
    """Return the cost in dollars of a list of (input tokens, output tokens) calls."""
    input_tokens = sum(call[0] for call in calls)
    output_tokens = sum(call[1] for call in calls)
    return (input_tokens * HAIKU_INPUT_PRICE + output_tokens * HAIKU_OUTPUT_PRICE) / 1_000_000


def measure_generation(client: Anthropic) -> dict[str, Any]:
    """Generate a description for every column without storing it, as medallion-describe would."""
    recorder = RecordingClient(client)
    columns = retried = rejected = 0
    start = time.perf_counter()

    for schema, table_names in ALLOWED_TABLES.items():
        for table_name in sorted(table_names):
            sample_rows = get_sample_rows(table_name, schema, limit=SAMPLE_ROWS_LIMIT)
            for row in get_schema(table_name, schema):
                calls_before = len(recorder.calls)
                try:
                    generate_description_with_guardrail(
                        recorder,
                        table_name=table_name,
                        schema=schema,
                        column_name=row["column_name"],
                        data_type=row["data_type"],
                        sample_values=sample_values_for_column(sample_rows, row["column_name"]),
                    )
                except ValueError as exc:
                    logger.warning("%s.%s: %s", table_name, row["column_name"], exc)
                    rejected += 1
                columns += 1
                retried += len(recorder.calls) - calls_before - 1

    elapsed = time.perf_counter() - start
    cost = anthropic_cost(recorder.calls)
    return {
        "columns": columns,
        "elapsed_seconds": round(elapsed, 1),
        "seconds_per_column": round(elapsed / columns, 2),
        "input_tokens": sum(call[0] for call in recorder.calls),
        "output_tokens": sum(call[1] for call in recorder.calls),
        "cost_usd": round(cost, 5),
        "cost_per_column_usd": round(cost / columns, 6),
        "guardrail_retries": retried,
        "guardrail_rejections": rejected,
    }


def measure_index_build() -> dict[str, Any]:
    """Time a full index build and an unchanged re-run, using the real code on a temporary folder."""
    with (
        tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder,
        patch.object(build_index, "CHROMA_DB_PATH", folder),
        patch.object(build_index, "mark_rows_embedded"),
        record_voyage_tokens() as tokens,
    ):
        start = time.perf_counter()
        build_index.run()
        full_build = time.perf_counter() - start

        start = time.perf_counter()
        build_index.run()
        unchanged_run = time.perf_counter() - start

    return {
        "full_build_seconds": round(full_build, 1),
        "unchanged_run_seconds": round(unchanged_run, 1),
        "voyage_tokens": sum(tokens),
        "cost_usd": round(sum(tokens) * VOYAGE_PRICE / 1_000_000, 6),
    }


def measure_questions(client: Anthropic, questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Run each question through retrieval and answering, the same calls the chatbot makes."""
    measured: list[dict[str, Any]] = []
    for question in questions:
        time.sleep(VOYAGE_SPACING_SECONDS)
        recorder = RecordingClient(client)

        with record_voyage_tokens() as tokens:
            start = time.perf_counter()
            results = retrieve(question["question"], top_k=TOP_K)
            retrieve_seconds = time.perf_counter() - start

        start = time.perf_counter()
        answer = generate_answer(recorder, question["question"], results)
        answer_seconds = time.perf_counter() - start

        retrieved = [f"{r['table_name']}.{r['column_name']}" for r in results]
        hit_at_1, hit_at_top_k = score_retrieval(question["expected"], question["match"], retrieved)
        measured.append(
            {
                **question,
                "retrieved": retrieved,
                "hit_at_1": hit_at_1,
                "hit_at_top_k": hit_at_top_k,
                "retrieve_seconds": round(retrieve_seconds, 2),
                "answer_seconds": round(answer_seconds, 2),
                "voyage_tokens": sum(tokens),
                "input_tokens": recorder.calls[0][0],
                "output_tokens": recorder.calls[0][1],
                "cost_usd": round(
                    anthropic_cost(recorder.calls) + sum(tokens) * VOYAGE_PRICE / 1_000_000, 6
                ),
                "answer": answer,
            }
        )
        logger.info("%s: hit@1=%s top_k=%s", question["id"], hit_at_1, hit_at_top_k)
    return measured


def main() -> int:
    """CLI entry point. Saves after each stage, so a later failure doesn't lose paid-for results."""
    configure_logging()
    questions = json.loads(QUESTIONS_PATH.read_text())
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    results: dict[str, Any] = {
        "measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "models": {"description": DESCRIPTION_MODEL, "chat": CHAT_MODEL, "embedding": VOYAGE_MODEL},
        "prices_usd_per_million_tokens": {
            "haiku_input": HAIKU_INPUT_PRICE,
            "haiku_output": HAIKU_OUTPUT_PRICE,
            "voyage": VOYAGE_PRICE,
        },
        "top_k": TOP_K,
    }

    results["generation"] = measure_generation(client)
    RESULTS_PATH.write_text(json.dumps(results, indent=2) + "\n")

    time.sleep(VOYAGE_SPACING_SECONDS)
    results["index"] = measure_index_build()
    RESULTS_PATH.write_text(json.dumps(results, indent=2) + "\n")

    measured = measure_questions(client, questions)
    results["summary"] = summarize_questions(measured)
    results["cost_per_question_usd"] = round(statistics.mean(row["cost_usd"] for row in measured), 6)
    results["questions"] = measured
    RESULTS_PATH.write_text(json.dumps(results, indent=2) + "\n")

    logger.info("Wrote %s", RESULTS_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
