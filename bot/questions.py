"""Extracts questions from chat messages."""

import logging
from typing import Optional

from telegram import Message, MessageEntity
from telegram.ext import CallbackContext

from bot import shortcuts
from bot.filters import Filters

logger = logging.getLogger(__name__)

# Создаем экземпляр фильтров
filters = Filters()


async def extract_private(message: Message, context: CallbackContext) -> Optional[str]:
    """Extracts a question from a private message."""
    # allow any messages in a private chat
    question = await _extract_text(message, context)
    if message.reply_to_message:
        # it's a follow-up question
        question = f"+ {question}"
    return question


async def extract_group(message: Message, context: CallbackContext) -> tuple[str, Message]:
    """Extracts a question from a group chat message."""
    base_text = message.text or message.caption or ""
    doc_suffix = ""
    if message.document:
        file = await context.bot.get_file(message.document.file_id)
        content = await file.download_as_bytearray()
        try:
            decoded = content.decode()
        except Exception:
            decoded = ""
        if decoded:
            doc_suffix = f"\n\n{message.document.file_name}:\n```\n{decoded}\n```"
    text = base_text + doc_suffix

    is_bot_mentioned = filters.is_bot_mentioned(message, context.bot.username)
    is_reply_to_bot = filters.is_reply_to_bot(message, context.bot.username)

    if not (is_bot_mentioned or is_reply_to_bot):
        return "", message

    if is_reply_to_bot:
        return (f"+ {text}" if text else "", message)

    if is_bot_mentioned:
        clean = base_text
        for entity in message.entities or message.caption_entities or []:
            if entity.type == MessageEntity.MENTION:
                clean = (
                    clean[: entity.offset] + clean[entity.offset + entity.length :]
                ).strip()
        text = clean + doc_suffix

        if message.reply_to_message:
            reply_text = await _extract_text(message.reply_to_message, context)
            return (f"{text}: {reply_text}" if text else reply_text, message.reply_to_message)

        return text, message

    return "", message


def extract_prev(message: Message, context: CallbackContext) -> str:
    """Extracts the previous message by the bot, if any."""
    if (
        message.reply_to_message
        and message.reply_to_message.from_user.username == context.bot.username
    ):
        # treat a reply to the bot as a follow-up question
        return message.reply_to_message.text

    # otherwise, ignore previous messages
    return ""


def prepare(question: str) -> tuple[str, bool]:
    """
    Returns the question without the special commands
    and indicates whether it is a follow-up.
    """

    if question[0] == "+":
        question = question.strip("+ ")
        is_follow_up = True
    else:
        is_follow_up = False

    if question[0] == "!":
        # this is a shortcut, so the bot should
        # process the question before asking it
        shortcut, question = shortcuts.extract(question)
        question = shortcuts.apply(shortcut, question)

    elif question[0] == "/":
        # this is a command, so the bot should
        # strip it from the question before asking
        _, _, question = question.partition(" ")
        question = question.strip()

    return question, is_follow_up


async def _extract_text(message: Message, context: CallbackContext) -> str:
    """Extracts text from a text message or a document message."""
    text = message.text or message.caption or ""

    if message.document:
        file = await context.bot.get_file(message.document.file_id)
        content = await file.download_as_bytearray()
        try:
            decoded = content.decode()
        except Exception:
            decoded = ""
        if decoded:
            text += f"\n\n{message.document.file_name}:\n```\n{decoded}\n```"
    return text
