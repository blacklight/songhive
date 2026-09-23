"""
CLI module: admin commands and utilities.
"""

import argparse


def build_parser() -> argparse.ArgumentParser:
    """Build the root ``songhive`` argument parser.

    Extends the core server-option parser with the ``admin``,
    ``stream-worker`` and ``watch-external-libraries`` subcommands so that
    ``songhive --help`` lists them.
    """
    from ..config.loader import build_cli_parser
    from . import admin, stream_worker, watch

    parser = build_cli_parser()
    subparsers = parser.add_subparsers(
        dest="subcommand",
        title="commands",
        help="run a command instead of starting the server",
    )
    admin.add_parser(subparsers)
    stream_worker.add_parser(subparsers)
    watch.add_parser(subparsers)
    return parser
