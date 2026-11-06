"""Tests for the banned-phrase guardrail that retries speculative bronze column descriptions."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from medallion_rag.subagents.generate_descriptions import (
    find_banned_phrase,
    generate_description_with_guardrail,
)

CLEAN = "Customer name as received from the source system."
SPECULATIVE = "A customer name that appears to be Hashed before ingestion."
ALSO_SPECULATIVE = "A customer name that may capture several source formats."


def client_returning(*descriptions: str) -> MagicMock:
    """Build a mocked Anthropic client that returns the given descriptions in order."""
    client = MagicMock()
    client.messages.create.side_effect = [
        SimpleNamespace(content=[SimpleNamespace(text=text)]) for text in descriptions
    ]
    return client


def generate(client: MagicMock, schema: str = "1_bronze", data_type: str = "STRING") -> str:
    """Run the guardrail for one column with fixed sample values."""
    return generate_description_with_guardrail(
        client,
        table_name="raw_customers",
        schema=schema,
        column_name="c_name",
        data_type=data_type,
        sample_values=["Customer#000000001"],
    )


def test_find_banned_phrase_is_case_insensitive() -> None:
    """Capitalized model output must still trip the filter."""
    assert find_banned_phrase(SPECULATIVE) == "hashed"
    assert find_banned_phrase(CLEAN) is None


def test_clean_bronze_string_description_is_not_retried() -> None:
    """A description without banned phrases costs a single API call."""
    client = client_returning(CLEAN)

    assert generate(client) == CLEAN
    assert client.messages.create.call_count == 1


@pytest.mark.parametrize("data_type", ["STRING", "varchar(25)"])
def test_banned_phrase_on_bronze_text_column_retries_and_names_the_phrase(data_type: str) -> None:
    """The corrective prompt quotes both the phrase and the rejected description."""
    client = client_returning(SPECULATIVE, CLEAN)

    assert generate(client, data_type=data_type) == CLEAN
    assert client.messages.create.call_count == 2
    corrective_prompt = client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert '"hashed"' in corrective_prompt
    assert SPECULATIVE in corrective_prompt


@pytest.mark.parametrize(
    ("schema", "data_type"), [("2_silver", "STRING"), ("3_gold", "STRING"), ("1_bronze", "DECIMAL")]
)
def test_guardrail_only_applies_to_bronze_text_columns(schema: str, data_type: str) -> None:
    """Other layers and types keep the first description even if it contains a banned phrase."""
    client = client_returning(SPECULATIVE)

    assert generate(client, schema=schema, data_type=data_type) == SPECULATIVE
    assert client.messages.create.call_count == 1


def test_retry_happens_once_and_flags_a_repeat_offender(caplog: pytest.LogCaptureFixture) -> None:
    """A second banned phrase is kept and flagged for review, not retried indefinitely."""
    client = client_returning(SPECULATIVE, ALSO_SPECULATIVE)

    assert generate(client) == ALSO_SPECULATIVE
    assert client.messages.create.call_count == 2
    assert "flagging for manual review" in caplog.text
