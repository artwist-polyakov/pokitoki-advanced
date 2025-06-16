"""Voice message handler."""

import logging
from typing import Awaitable

import tempfile
from pathlib import Path

from telegram import Chat, Update
from telegram.ext import CallbackContext

from bot.voice import VoiceProcessor

logger = logging.getLogger(__name__)


class VoiceMessage:
    """Processes voice messages."""

    def __init__(self, reply_func: Awaitable) -> None:
        self.reply_func = reply_func
        self.processor = VoiceProcessor()

    async def __call__(self, update: Update, context: CallbackContext) -> None:
        message = update.message or update.edited_message

        # In group chats, only process replies to bot messages
        is_group = message.chat.type != Chat.PRIVATE
        is_reply_to_bot = (
            message.reply_to_message
            and message.reply_to_message.from_user
            and message.reply_to_message.from_user.username == context.bot.username
        )

        if is_group and not is_reply_to_bot:
            logger.info("Ignoring voice message in group chat - not a reply to bot")
            return

        voice_file = await message.voice.get_file()
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp_file:
            await voice_file.download_to_drive(tmp_file.name)
            voice_path = Path(tmp_file.name)

        question = await self.processor.transcribe(voice_path)
        voice_path.unlink()

        if not question:
            await message.reply_text("Sorry, I couldn't understand the voice message.")
            return

        await self.reply_func(
            update=update,
            message=message,
            context=context,
            question=question,
        )
