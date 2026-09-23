"""
Tests for the root ``songhive`` argument parser.

The root parser combines the core server options with the dedicated CLI
subcommands (``admin``, ``stream-worker``, ``watch-external-libraries``) so
that ``songhive --help`` lists them.
"""

import pytest

from songhive.cli import build_parser


def test_root_parser_lists_subcommands():
    """The root parser help lists every CLI subcommand."""
    help_text = build_parser().format_help()
    assert "admin" in help_text
    assert "stream-worker" in help_text
    assert "watch-external-libraries" in help_text


def test_root_parser_parses_admin_subcommand():
    """Admin subcommands parse through the root parser."""
    args = build_parser().parse_args(["admin", "init-db"])
    assert args.subcommand == "admin"
    assert args.command == "init-db"


def test_root_parser_parses_nested_admin_options():
    """Nested admin command options parse through the root parser."""
    args = build_parser().parse_args(
        ["admin", "create-user", "--username", "alice", "--email", "a@b.c", "--password", "pw"]
    )
    assert args.subcommand == "admin"
    assert args.command == "create-user"
    assert args.username == "alice"


def test_root_parser_parses_stream_worker():
    """The stream-worker subcommand parses through the root parser."""
    args = build_parser().parse_args(["stream-worker", "--debug"])
    assert args.subcommand == "stream-worker"
    assert args.debug is True


def test_root_parser_parses_watch_external_libraries():
    """The watch-external-libraries subcommand parses through the root parser."""
    args = build_parser().parse_args(["watch-external-libraries"])
    assert args.subcommand == "watch-external-libraries"


def test_root_parser_no_subcommand():
    """Server options without a subcommand leave ``subcommand`` unset."""
    args = build_parser().parse_args(["--port", "9999"])
    assert args.subcommand is None
    assert args.port == 9999


def test_root_parser_rejects_unknown_command():
    """An unknown command exits with a parse error."""
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args(["bogus"])
    assert exc_info.value.code == 2
