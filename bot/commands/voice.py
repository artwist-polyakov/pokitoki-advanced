"""Voice message handler."""

import logging
from typing import Awaitable

from telegram import Chat, Update
from telegram.ext import CallbackContext

logger = logging.getLogger(__name__)


class VoiceMessage:
    """Processes voice messages."""

    def __init__(self, reply_func: Awaitable) -> None:
        self.reply_func = reply_func

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

        await self.reply_func(
            update=update,
            message=message,
            context=context,
            question="",  # Question will be extracted from the voice message in reply_func
        )
