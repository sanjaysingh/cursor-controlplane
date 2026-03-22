"""ACP/session text extraction (assistant-visible content vs metadata-only results)."""

from __future__ import annotations

from control_plane.session_manager import (
    _extract_text_from_acp_update,
    _text_from_session_prompt_result,
)


def test_text_from_session_prompt_result_prefers_text_keys():
    assert _text_from_session_prompt_result({"text": " hello "}) == "hello"
    assert _text_from_session_prompt_result({"stopReason": "end"}) == ""


def test_extract_text_from_acp_update_chunk_mode():
    upd = {
        "sessionUpdate": "agent_message_chunk",
        "content": {"text": "partial"},
    }
    assert (
        _extract_text_from_acp_update(upd, mode="agent_message_chunk_only") == "partial"
    )
    assert _extract_text_from_acp_update({"sessionUpdate": "other"}, mode="agent_message_chunk_only") == ""
