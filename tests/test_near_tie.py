"""Tests for how the chatbot words its answer when the top two retrieval matches are near-tied."""

from __future__ import annotations

import pytest

from medallion_rag.chatbot.app import NEAR_TIE_THRESHOLD, format_context, near_tie_instruction

BRONZE = {"table_name": "raw_orders", "column_name": "o_orderpriority", "description": "Raw priority."}
SILVER = {"table_name": "orders", "column_name": "order_priority", "description": "Clean priority."}
SILVER_OTHER = {"table_name": "customers", "column_name": "market_segment", "description": "Segment."}
GOLD = {"table_name": "customer_ltv", "column_name": "lifetime_revenue", "description": "Revenue."}
UNKNOWN = {"table_name": "not_in_lakehouse", "column_name": "mystery", "description": "Unknown."}


def ranked(first: dict[str, str], second: dict[str, str], gap: float = 0.01) -> list[dict[str, str | float]]:
    """Build two retrieval results, closest first, separated by a distance gap."""
    return [{**first, "distance": 0.5}, {**second, "distance": 0.5 + gap}]


def test_context_includes_lineage_only_when_known() -> None:
    """The model can answer lineage questions, and sees no empty lineage clause otherwise."""
    context = format_context(
        [
            {**SILVER, "derived_from": "1_bronze.raw_orders.o_orderpriority"},
            {**BRONZE, "derived_from": ""},
        ]
    )

    assert "Clean priority. Derived from: 1_bronze.raw_orders.o_orderpriority." in context
    assert context.count("Derived from") == 1


def test_single_result_has_nothing_to_tie_with() -> None:
    """One match cannot be a tie."""
    assert near_tie_instruction(ranked(BRONZE, SILVER)[:1]) is None


def test_no_instruction_when_gap_exceeds_threshold() -> None:
    """A clear winner is left for the model to answer from directly."""
    assert near_tie_instruction(ranked(BRONZE, SILVER, gap=NEAR_TIE_THRESHOLD + 0.01)) is None


@pytest.mark.parametrize(("first", "second"), [(BRONZE, SILVER), (SILVER, BRONZE)])
def test_bronze_silver_tie_recommends_silver_in_either_rank_order(
    first: dict[str, str], second: dict[str, str]
) -> None:
    """The cleaned layer is recommended whichever of the two ranked first."""
    instruction = near_tie_instruction(ranked(first, second))

    assert instruction is not None
    assert "raw_orders.o_orderpriority" in instruction
    assert "orders.order_priority" in instruction
    assert "the 2_silver" not in instruction
    assert "the silver version is the one to use" in instruction


@pytest.mark.parametrize(("first", "second"), [(GOLD, SILVER), (SILVER, GOLD)])
def test_gold_tie_leaves_the_choice_to_the_user(first: dict[str, str], second: dict[str, str]) -> None:
    """A gold aggregate is not a refined copy of its source, so neither column is recommended."""
    instruction = near_tie_instruction(ranked(first, second))

    assert instruction is not None
    assert "customer_ltv.lifetime_revenue" in instruction
    assert "orders.order_priority" in instruction
    assert "let the user judge" in instruction
    assert "the one to use" not in instruction


@pytest.mark.parametrize(("first", "second"), [(SILVER, SILVER_OTHER), (SILVER, UNKNOWN)])
def test_same_layer_or_unknown_table_only_asks_to_mention_both(
    first: dict[str, str], second: dict[str, str]
) -> None:
    """Without two known, different layers there is no basis for recommending either column."""
    instruction = near_tie_instruction(ranked(first, second))

    assert instruction is not None
    assert f"{first['table_name']}.{first['column_name']}" in instruction
    assert f"{second['table_name']}.{second['column_name']}" in instruction
    assert "rather than only the closer one" in instruction
