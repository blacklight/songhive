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


def _add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--debug",
        action="store_true",
        default=False,
        help="Enable debug logging",
    )


def _create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="songhive stream-worker")
    _add_arguments(parser)
    return parser


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """Register the ``stream-worker`` command on the root ``songhive`` parser."""
    parser = subparsers.add_parser(
        "stream-worker",
        help="Run the stream worker for server-side audio outputs",
    )
    _add_arguments(parser)
    return parser


def stream_worker_main(argv=None) -> None:
    """Parse CLI arguments and start the stream worker."""
    args = _create_parser().parse_args(argv)

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
