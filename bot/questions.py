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


async def extract_group(message: Message, context: CallbackContext) -> str:
    """Извлекает вопрос из сообщения в групповом чате."""
    text = await _extract_text(message, context)

    # Проверяем взаимодействие с ботом
    is_bot_mentioned = filters.is_bot_mentioned(message, context.bot.username)
    is_reply_to_bot = filters.is_reply_to_bot(message, context.bot.username)

    if not (is_bot_mentioned or is_reply_to_bot):
        return ""

    # Если это ответ боту
    if is_reply_to_bot:
        return f"+ {text}" if text else ""

    # Если это упоминание бота
    if is_bot_mentioned:
        # Убираем упоминание из текста
        for entity in message.entities or message.caption_entities or []:
            if entity.type == MessageEntity.MENTION:
                text = (
                    text[: entity.offset] + text[entity.offset + entity.length :]
                ).strip()

        # Если это ответ на сообщение
        if message.reply_to_message:
            reply_text = await _extract_text(message.reply_to_message, context)
            return f"{text}: {reply_text}" if text else reply_text

        return text

    return ""


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
    if message.text:
        return message.text
    if message.caption:
        return message.caption
    return ""
