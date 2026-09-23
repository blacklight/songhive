"""
CLI entry point for the stream worker.

Usage:
    songhive stream-worker [--debug]
"""

import argparse
import asyncio
import logging
import os
from pathlib import Path

from ..config import load_config
from ..streams.worker import run_stream_worker


def stream_worker_main(argv=None) -> None:
    """Parse CLI arguments and start the stream worker."""
    parser = argparse.ArgumentParser(prog="songhive stream-worker")
    parser.add_argument(
        "--debug",
        action="store_true",
        default=False,
        help="Enable debug logging",
    )
    args = parser.parse_args(argv)

    # If no auth secret is configured, try the one persisted by the Docker
    # entrypoint so that `docker compose exec ... songhive stream-worker` works
    # without an explicit SONGHIVE_AUTH__SECRET_KEY variable.
    if not os.environ.get("SONGHIVE_AUTH__SECRET_KEY"):
        secret_file = Path(os.environ.get("SONGHIVE_SECRET_FILE", "/data/secret_key"))
        if secret_file.exists():
            os.environ["SONGHIVE_AUTH__SECRET_KEY"] = secret_file.read_text().strip()

    load_config([])

    level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    try:
        asyncio.run(run_stream_worker())
    except KeyboardInterrupt:
        pass
