"""Tests for how the evaluation scores retrieval and summarizes results."""

from __future__ import annotations

import json

import pytest

from medallion_rag.evaluation.run_eval import (
    GRADES_PATH,
    QUESTIONS_PATH,
    anthropic_cost,
    score_retrieval,
    summarize_questions,
)


@pytest.mark.parametrize(
    ("expected", "match", "retrieved", "scores"),
    [
        (["a"], "any", ["a", "b"], (True, True)),
        (["a"], "any", ["b", "a"], (False, True)),
        (["a"], "any", ["b", "c"], (False, False)),
        (["a", "b"], "any", ["c", "b"], (False, True)),
        (["a", "b"], "all", ["a", "b"], (True, True)),
        (["a", "b"], "all", ["b", "c", "a"], (True, True)),
        (["a", "b"], "all", ["a", "c"], (True, False)),
        ([], "none", ["a"], (None, None)),
    ],
)
def test_score_retrieval(
    expected: list[str], match: str, retrieved: list[str], scores: tuple[bool | None, bool | None]
) -> None:
    """A "not in the dictionary" question has no retrieval score, and "all" needs every column."""
    assert score_retrieval(expected, match, retrieved) == scores


def test_summary_leaves_unscored_questions_out_of_rates_but_not_latency() -> None:
    """Questions with nothing to retrieve still count towards latency."""
    rows = [
        {
            "category": name,
            "hit_at_1": first,
            "hit_at_top_k": top_k,
            "retrieve_seconds": seconds,
            "answer_seconds": seconds,
        }
        for name, first, top_k, seconds in [
            ("direct", True, True, 1),
            ("direct", False, True, 2),
            ("none", None, None, 3),
        ]
    ]
    summary = summarize_questions(rows)

    assert summary["overall"] == {"questions": 2, "hit_at_1": 1, "hit_at_top_k": 2}
    assert summary["by_category"]["none"]["questions"] == 0
    assert summary["median_latency_seconds"] == 4
    assert summary["max_latency_seconds"] == 6


def test_cost_prices_input_and_output_tokens_separately() -> None:
    """One million of each token type costs the sum of the two list prices."""
    assert anthropic_cost([(400_000, 400_000), (600_000, 600_000)]) == pytest.approx(6.00)


def test_committed_questions_are_well_formed() -> None:
    """Every question must be scoreable, so a typo can't silently drop one from the results."""
    questions = json.loads(QUESTIONS_PATH.read_text())

    assert len({question["id"] for question in questions}) == len(questions)
    for question in questions:
        assert question["match"] in {"any", "all", "none"}
        assert bool(question["expected"]) == (question["match"] != "none")
        assert question["answer_must"]


def test_every_question_has_a_grade() -> None:
    """The README's answer figures come from these grades, so none can be missing."""
    questions = json.loads(QUESTIONS_PATH.read_text())
    grades = json.loads(GRADES_PATH.read_text())["grades"]

    assert set(grades) == {question["id"] for question in questions}
