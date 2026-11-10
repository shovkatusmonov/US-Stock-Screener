"""
tests/test_cli.py - Tests for Unified CLI Parser and Subcommands.
"""

import sys
import pytest
from main import build_parser, cmd_universe, cmd_screen, cmd_health
import argparse


def test_cli_parser_subcommands():
    parser = build_parser()
    subparsers_actions = [
        action for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    ]
    assert len(subparsers_actions) == 1
    choices = subparsers_actions[0].choices
    for cmd in ["universe", "data", "screen", "quote", "breadth", "health", "bot", "scheduler"]:
        assert cmd in choices


def test_cli_screen_list_presets(capsys):
    parser = build_parser()
    args = parser.parse_args(["screen", "--list-presets"])
    res = cmd_screen(args)
    captured = capsys.readouterr()
    assert res == 0
    assert "bullish_breakout" in captured.out
    assert "oversold_bounce" in captured.out


def test_cli_health(capsys):
    parser = build_parser()
    args = parser.parse_args(["health"])
    res = cmd_health(args)
    captured = capsys.readouterr()
    assert res == 0
    assert "SYSTEM HEALTH CHECK" in captured.out
    assert "Process RSS RAM" in captured.out
