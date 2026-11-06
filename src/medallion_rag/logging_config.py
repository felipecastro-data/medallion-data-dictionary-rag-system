"""Logging setup shared by the command-line entry points."""

from __future__ import annotations

import logging


def configure_logging() -> None:
    """Log this project at INFO and keep third-party libraries at WARNING.

    `force` and the explicit level undo the logging setup FastMCP applies when it is imported.
    `__main__` is included because modules run with `python -m` log under that name.
    """
    logging.basicConfig(
        level=logging.WARNING, format="%(asctime)s %(levelname)-7s %(message)s", force=True
    )
    for name in ("medallion_rag", "__main__"):
        logging.getLogger(name).setLevel(logging.INFO)
    # chromadb 0.5.20 calls an incompatible posthog API and logs each failed telemetry event as
    # an error, even though indexing and retrieval are unaffected.
    logging.getLogger("chromadb.telemetry.product.posthog").setLevel(logging.CRITICAL)
