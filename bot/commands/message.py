"""Text message handler."""

import logging
from typing import Awaitable
from pathlib import Path
import tempfile
from datetime import datetime, timedelta

from telegram import Chat, Update
from telegram.ext import CallbackContext

from bot import questions
from bot.file_processor import FileProcessor
from bot.config import config
from bot.models import UserData
from bot.filters import Filters
from bot.voice import VoiceProcessor
from bot.models import ParsedMessage
from bot.commands.parsemany import active_sessions

logger = logging.getLogger(__name__)


class MessageCommand:
    """Answers a question from the user."""

    def __init__(self, reply_func: Awaitable) -> None:
        self.reply_func = reply_func
        self.voice_processor = VoiceProcessor()
        self.filters = Filters()
        self.last_message_time = {}  # Для хранения времени последнего сообщения
        self.grouped_messages = {}  # Для хранения сгруппированных сообщений
        self.GROUP_WINDOW = timedelta(seconds=3)  # Окно группировки - 3 секунды

    async def __call__(self, update: Update, context: CallbackContext) -> None:
        message = update.message
        chat_id = message.chat.id
        current_time = datetime.now()

        # Проверяем, есть ли активная сессия парсинга
        parsing_session = active_sessions.get(chat_id)
        is_parsing_mode = parsing_session is not None

        # Обработка пересланных сообщений
        if message.forward_date:
            # Проверяем, было ли предыдущее сообщение недавно
            last_time = self.last_message_time.get(chat_id)

            # Всегда добавляем сообщение в группу
            if chat_id not in self.grouped_messages:
                self.grouped_messages[chat_id] = []
                self.last_message_time[chat_id] = current_time

            # Формируем контекст сообщения
            forwarded_from = (
                message.forward_from.username
                if message.forward_from
                else message.forward_sender_name or "Unknown"
            )
            forward_date = message.forward_date.strftime("%Y-%m-%d %H:%M:%S")
            content = message.text or message.caption or ""

            # Добавляем информацию об отправителе в начало контента
            msg_text = (
                f"Forwarded from {forwarded_from} at {forward_date}:\n{content}\n"
            )

            # Обрабатываем файлы, если есть
            if message.document or message.photo:
                file_processor = FileProcessor()
                file_content = await file_processor.process_files(
                    documents=[message.document] if message.document else [],
                    photos=message.photo if message.photo else [],
                )
                if file_content:
                    if is_parsing_mode:
                        if message.document:
                            filename = message.document.file_name
                            attached_files = [filename]
                        else:
                            # Обрабатываем все фото в сообщении
                            attached_files = []
                            for photo in message.photo[
                                -1:
                            ]:  # Берем только самое большое фото
                                filename = f"image_{photo.file_unique_id}"
                                parsing_session.state.file_contents[filename] = (
                                    file_content
                                )
                                attached_files.append(filename)
                        msg_text += f"\nAttached files: {', '.join(attached_files)}"
                    else:
                        msg_text += f"\n\n{file_content}"

            self.grouped_messages[chat_id].append(msg_text)

            # Если прошло больше времени окна, обрабатываем группу
            if not last_time or (current_time - last_time) > self.GROUP_WINDOW:
                grouped_text = "\n".join(self.grouped_messages[chat_id])
                del self.grouped_messages[chat_id]
                del self.last_message_time[chat_id]  # Очищаем таймер

                if is_parsing_mode:
                    # Добавляем группу в парсинг сессию
                    timestamp = datetime.fromtimestamp(message.forward_date.timestamp())
                    parsed_message = ParsedMessage(
                        sender_id=update.effective_user.username
                        or "Anonymous",  # Безопасное значение
                        timestamp=timestamp,
                        content=grouped_text,
                        attached_files=[],
                    )
                    parsing_session.state.messages.append(parsed_message)
                else:
                    # Обрабатываем группу как одно сообщение с контекстом
                    await self.reply_func(
                        update=update,
                        message=message,
                        context=context,
                        question=grouped_text,  # Передаем полный контекст
                    )
            return

        # Остальной код обработки обычных сообщений остается без изменений...

        # Извлекаем текст и файлы
        question = ""
        file_content = None

        if message.text:
            question = message.text
        elif message.caption:
            question = message.caption

        # Обрабатываем файлы и фото
        if message.document or message.photo:
            if is_parsing_mode:
                # В режиме парсинга просто добавляем файл в state
                file_processor = FileProcessor()
                file_content = await file_processor.process_files(
                    documents=[message.document] if message.document else [],
                    photos=message.photo if message.photo else [],
                )

                if file_content:
                    if message.document:
                        filename = message.document.file_name
                    else:
                        filename = f"image_{message.photo[-1].file_unique_id}"
                    parsing_session.state.file_contents[filename] = file_content
                    await message.reply_text(f"Added file: {filename}")
                return
            else:
                # Обычный режим - сначала сохраняем контент
                file_processor = FileProcessor()
                file_content = await file_processor.process_files(
                    documents=[message.document] if message.document else [],
                    photos=message.photo if message.photo else [],
                )
                if file_content:
                    # Сохраняем контент в данных пользователя
                    user = UserData(context.user_data)
                    user.data["last_file_content"] = file_content

                    # Спрашиваем что делать с файлом
                    await message.reply_text(
                        "This is a file. What should I do with it?"
                    )
                    return

        # Добавляем сообщение в парсинг сессию, если она активна
        if is_parsing_mode and (question or file_content):
            timestamp = datetime.fromtimestamp(message.date.timestamp())
            parsed_message = ParsedMessage(
                sender_id=message.from_user.username or str(message.from_user.id),
                timestamp=timestamp,
                content=question,
                attached_files=[],
            )
            parsing_session.state.messages.append(parsed_message)
            logger.info(
                f"Adding text message to parsing state. From: {parsed_message.sender_id}, Content: {question[:20]}..."
            )
            return

        # Обычная обработка сообщения
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
