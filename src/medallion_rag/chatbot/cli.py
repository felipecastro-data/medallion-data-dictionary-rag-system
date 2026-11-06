"""Console entry point for the chatbot, since a Streamlit app must start through `streamlit run`."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

APP_PATH = Path(__file__).with_name("app.py")


def main() -> None:
    """Launch the chatbot, forwarding extra arguments such as --server.port to Streamlit."""
    command = [sys.executable, "-m", "streamlit", "run", str(APP_PATH), *sys.argv[1:]]
    raise SystemExit(subprocess.call(command))
