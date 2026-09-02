"""
CLI entry point for the local external-library watchdog.

Usage:
    songhive watch-external-libraries [--debug]
"""

import argparse
import asyncio
import logging
import os
from pathlib import Path

from ..config import load_config
from ..external.watchdog import watch_external_libraries


def watch_main(argv=None) -> None:
    """Parse CLI arguments and start the watchdog."""
    parser = argparse.ArgumentParser(prog="songhive watch-external-libraries")
    parser.add_argument(
        "--debug",
        action="store_true",
        default=False,
        help="Enable debug logging",
    )
    args = parser.parse_args(argv)

    # If no auth secret is configured, try the one persisted by the Docker
    # entrypoint so that `docker compose exec ... songhive watch-...` works
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

    try:
        asyncio.run(watch_external_libraries())
    except KeyboardInterrupt:
        pass
