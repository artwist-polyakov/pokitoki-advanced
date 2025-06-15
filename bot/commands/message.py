"""Text message handler."""

import logging
from typing import Awaitable

from telegram import Chat, Update
from telegram.ext import CallbackContext

from bot.bot import _get_message_content
from bot.filters import Filters
from bot.models import UserData

logger = logging.getLogger(__name__)


class MessageCommand:
    """Organizes buffering and processing of messages."""

    def __init__(self, reply_func: Awaitable) -> None:
        self.reply_func = reply_func
        self.filters = Filters()

    async def __call__(self, update: Update, context: CallbackContext) -> None:
        message = update.message or update.edited_message
        user = UserData(context.user_data)

        is_group = message.chat.type != Chat.PRIVATE
        is_bot_mentioned = self.filters.is_bot_mentioned(message, context.bot.username)
        is_reply_to_bot = self.filters.is_reply_to_bot(message, context.bot.username)

        if is_group and not (is_bot_mentioned or is_reply_to_bot):
            return

        content = await _get_message_content(message)

        if message.forward_date and message.chat.type == Chat.PRIVATE:
            if content:
                user.add_to_forward_buffer(content)
                logger.info(f"Содержимое из пересланного сообщения {message.id} добавлено в буфер")
            return

        buffered_items = user.pop_recent_forward_buffer()
        final_question = content or ""
        if buffered_items:
            context_str = "\n\n---\n\n".join(buffered_items)
            final_question = f"<context>\n{context_str}\n</context>\n\n{final_question}".strip()

        if not final_question.strip():
            if buffered_items:
                await message.reply_text("Я получил ваши сообщения. Что бы вы хотели узнать?")
            return

        await self.reply_func(
            update=update, message=message, context=context, question=final_question
        )
