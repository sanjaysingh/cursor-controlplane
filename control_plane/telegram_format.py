"""Convert Markdown to Telegram Bot API text + MessageEntity (no parse_mode)."""

from __future__ import annotations

import logging

from aiogram.enums import MessageEntityType
from aiogram.types import MessageEntity

logger = logging.getLogger(__name__)


def markdown_to_telegram_plain_and_entities(text: str) -> tuple[str, list[MessageEntity] | None]:
    """
    Convert Markdown to (plain_text, entities) for Telegram.
    Returns (text, None) on failure, unknown entity types, or when plain text exceeds 4096
    (callers should send plain chunks without entities).
    """
    if not text:
        return "", None
    try:
        from telegramify_markdown import convert as tfm_convert
    except ImportError:
        logger.warning("telegramify-markdown is not installed; Telegram messages stay plain text")
        return text, None
    try:
        plain, ents = tfm_convert(text)
    except Exception as e:
        logger.debug("telegramify_markdown.convert failed: %s", e)
        return text, None
    if len(plain) > 4096:
        return plain, None
    if not ents:
        return plain, None
    out: list[MessageEntity] = []
    for e in ents:
        try:
            mt = MessageEntityType(e.type)
        except ValueError:
            logger.debug("skip unknown Telegram entity type %r", e.type)
            return plain, None
        out.append(
            MessageEntity(
                type=mt,
                offset=e.offset,
                length=e.length,
                url=getattr(e, "url", None),
                language=getattr(e, "language", None),
                custom_emoji_id=getattr(e, "custom_emoji_id", None),
            )
        )
    return plain, out
