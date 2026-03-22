"""Model id normalization for `agent --model` (CLI list lines vs ids)."""

from __future__ import annotations

from control_plane.model_cli import (
    cli_model_id_for_argv,
    is_placeholder_cli_model_id,
    split_model_display_line,
)


def test_split_model_display_line():
    assert split_model_display_line("gpt-4 - Fast model") == ("gpt-4", "Fast model")
    assert split_model_display_line("no-separator") is None


def test_cli_model_id_for_argv_strips_display_suffix():
    assert cli_model_id_for_argv("composer-1 — Display Name") == "composer-1"
    assert cli_model_id_for_argv("plain-id") == "plain-id"


def test_placeholder_ids_become_none():
    assert is_placeholder_cli_model_id("current") is True
    assert cli_model_id_for_argv("current") is None
