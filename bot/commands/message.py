"""Text message handler."""

import logging
from typing import Awaitable
from pathlib import Path
import tempfile

from telegram import Chat, Update
from telegram.ext import CallbackContext

from bot import questions
from bot.file_processor import FileProcessor
from bot.config import config
from bot.models import UserData
from bot.filters import Filters
from bot.voice import VoiceProcessor

logger = logging.getLogger(__name__)


class MessageCommand:
    """Answers a question from the user."""

    def __init__(self, reply_func: Awaitable) -> None:
        self.reply_func = reply_func
        self.voice_processor = VoiceProcessor()
        self.filters = Filters()

    async def __call__(self, update: Update, context: CallbackContext) -> None:
        message = update.message or update.edited_message
        logger.info(
            f"Message handler called: "
            f"from={update.effective_user.username}, "
            f"text={bool(message.text)}, "
            f"voice={bool(message.voice)}, "
            f"document={message.document.file_name if message.document else None}, "
            f"photo={bool(message.photo)}, "
            f"caption={bool(message.caption)}"
        )

        # Проверяем, групповой ли это чат
        is_group = message.chat.type != Chat.PRIVATE

        # Проверяем взаимодействие с ботом
        is_bot_mentioned = self.filters.is_bot_mentioned(message, context.bot.username)
        is_reply_to_bot = self.filters.is_reply_to_bot(message, context.bot.username)

        # В групповом чате обрабатываем только сообщения с упоминанием бота или ответы боту
        if is_group and not (is_bot_mentioned or is_reply_to_bot):
            logger.info("Ignoring message in group chat - no bot interaction")
            return

        # Обработка голосовых сообщений
        if message.voice:
            if is_group and not is_reply_to_bot:
                logger.info("Ignoring voice message in group chat - not a reply to bot")
                return

        # Обработка файлов
        file_content = None
        if (message.document or message.photo) and config.files.enabled:
            if is_group and not is_reply_to_bot:
                logger.info("Ignoring file in group chat - not a reply to bot")
                return

            file_processor = FileProcessor()
            file_content = await file_processor.process_files(
                documents=[message.document] if message.document else [],
                photos=message.photo if message.photo else [],
            )

        # Получаем текст сообщения
        if message.chat.type == Chat.PRIVATE:
            question = await questions.extract_private(message, context)
        else:
            question, message = await questions.extract_group(message, context)

            # Если это ответ с упоминанием бота на сообщение с файлом/голосовым
            if (
                is_bot_mentioned
                and message.reply_to_message
                and (
                    message.reply_to_message.voice
                    or message.reply_to_message.document
                    or message.reply_to_message.photo
                )
            ):

                if message.reply_to_message.voice:
                    # Обработка голосового из reply
                    voice_file = await message.reply_to_message.voice.get_file()
                    with tempfile.NamedTemporaryFile(
                        suffix=".ogg", delete=False
                    ) as tmp_file:
                        await voice_file.download_to_drive(tmp_file.name)
                        voice_path = Path(tmp_file.name)

                    # Транскрибируем голосовое в текст
                    voice_content = await self.voice_processor.transcribe(voice_path)
                    voice_path.unlink()  # Очищаем

                    if voice_content:
                        file_content = voice_content
                    else:
                        await message.reply_text(
                            "Sorry, I couldn't understand the voice message."
                        )
                        return

                elif (
                    message.reply_to_message.document or message.reply_to_message.photo
                ):
                    # Обработка файла из reply
                    file_processor = FileProcessor()
                    file_content = await file_processor.process_files(
                        documents=(
                            [message.reply_to_message.document]
                            if message.reply_to_message.document
                            else []
                        ),
                        photos=(
                            message.reply_to_message.photo
                            if message.reply_to_message.photo
                            else []
                        ),
                    )

        # Обработка файлового контента
        if file_content and not question:
            user = UserData(context.user_data)
            user.data["last_file_content"] = file_content
            await message.reply_text("This is a file. What should I do with it?")
            return

        if question and not file_content:
            user = UserData(context.user_data)
            file_content = user.data.pop("last_file_content", None)
            if file_content:
                question = f"{question}\n\n{file_content}"

        if not file_content and not question:
            logger.info("No content extracted, ignoring message")
            return

        if file_content:
            question = f"{question}\n\n{file_content}" if question else file_content

        await self.reply_func(
            update=update, message=message, context=context, question=question
        )
