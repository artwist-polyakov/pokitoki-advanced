"""Extracts questions from chat messages."""

import logging
from typing import Optional

from telegram import Message, MessageEntity
from telegram.ext import CallbackContext

from bot import shortcuts

logger = logging.getLogger(__name__)


async def extract_private(message: Message, context: CallbackContext) -> Optional[str]:
    """Extracts a question from a private message."""
    # allow any messages in a private chat
    question = await _extract_text(message, context)
    if message.reply_to_message:
        # it's a follow-up question
        question = f"+ {question}"
    return question


async def extract_group(
    message: Message, context: CallbackContext
) -> tuple[str, Message]:
    """Extracts a question from a message in a group chat."""
    # Check if the message is a reply to the bot
    is_reply_to_bot = (
        message.reply_to_message
        and message.reply_to_message.from_user.username == context.bot.username
    )

    # Check for bot mention
    entities = message.entities or message.caption_entities
    mention = (
        entities[0] if entities and entities[0].type == MessageEntity.MENTION else None
    )
    has_bot_mention = False
    if mention:
        mention_text = message.text[mention.offset : mention.offset + mention.length]
        has_bot_mention = mention_text.lower() == context.bot.name.lower()

    # Check message type
    has_voice = bool(message.voice)
    has_file = bool(message.document)
    has_text = bool(message.text or message.caption)

    # Ignore if there's no reply to bot and no bot mention
    if not is_reply_to_bot and not has_bot_mention:
        return "", message

    # Get text from current message
    current_text = await _extract_text(message, context)

    # If this is a reply to the bot
    if is_reply_to_bot:
        # Process voice messages and files even without text
        if has_voice or has_file:
            return f"+ {current_text}" if current_text else "+", message
        # Text messages require text content
        if has_text:
            return f"+ {current_text}" if current_text else "", message
        return "", message

    # If the bot is mentioned
    if has_bot_mention:
        # Remove the mention from text
        question = (
            current_text[: mention.offset]
            + current_text[mention.offset + mention.length :]
        )
        question = question.strip()

        # If this is a reply to another message
        if (
            message.reply_to_message
            and not message.reply_to_message.forum_topic_created
        ):
            reply_msg = message.reply_to_message

            # Check the type of message being replied to
            reply_has_voice = bool(reply_msg.voice)
            reply_has_file = bool(reply_msg.document)

            if reply_has_voice or reply_has_file:
                # Special handling for voice messages and files
                reply_text = await _extract_text(reply_msg, context)
                return (
                    f"{question}: {reply_text}" if question else reply_text,
                    reply_msg,
                )

            # Same logic as before for text messages
            reply_text = await _extract_text(reply_msg, context)
            return (f"{question}: {reply_text}" if question else reply_text, reply_msg)

        return question, message

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
    if message.text:
        return message.text
    if message.caption:
        return message.caption
    return ""
