"""Tests that the chatbot explains an embedding rate limit instead of showing a traceback."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import voyageai
from streamlit.testing.v1 import AppTest

from medallion_rag.chatbot import app
from medallion_rag.embeddings import query


def test_rate_limit_shows_a_message_not_a_traceback(monkeypatch: pytest.MonkeyPatch) -> None:
    """The free Voyage tier allows 3 requests a minute, so a user can easily hit this limit."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(query, "retrieve", MagicMock(side_effect=voyageai.error.RateLimitError("slow down")))
    chat = AppTest.from_file(str(Path(app.__file__)), default_timeout=30)
    chat.run()

    chat.chat_input[0].set_value("What does order status mean?").run()

    assert not chat.exception
    assert "rate limit" in chat.error[0].value
