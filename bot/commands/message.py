"""Text message handler."""

import asyncio
import logging
from typing import Awaitable
from pathlib import Path
import tempfile
import os

from telegram import Chat, Update, Message
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
        self._buffers: dict[str, list[tuple[Update, Message]]] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._file_prompts: dict[str, asyncio.Task] = {}
        self.debounce_sec = float(os.getenv("DEBOUNCE_WINDOW", "1"))

    async def __call__(self, update: Update, context: CallbackContext) -> None:
        message = update.message or update.edited_message
        logger.info(
            f"Message handler called: "
            f"from={update.effective_user.username}, "
            f"text={bool(message.text)}, "
            f"voice={bool(message.voice)}, "
            f"document={message.document.file_name if message.document else None}, "
            f"photo={bool(message.photo)}, "
            f"caption={bool(message.caption)}, "
            f"media_group_id={message.media_group_id}"
        )

        key = message.media_group_id or f"{message.chat.id}:{update.effective_user.id}"
        self._buffers.setdefault(key, []).append((update, message))
        task = self._tasks.get(key)
        if task:
            task.cancel()
        self._tasks[key] = asyncio.create_task(self._flush(key, context))

    async def _flush(self, key: str, context: CallbackContext) -> None:
        try:
            await asyncio.sleep(self.debounce_sec)
        except asyncio.CancelledError:
            return
        data = self._buffers.pop(key, [])
        self._tasks.pop(key, None)
        if not data:
            return
        updates, messages = zip(*data)
        update = updates[-1]
        base = messages[-1]
        docs: list = []
        photos: list = []
        texts: list[str] = []
        for msg in messages:
            if msg.document:
                docs.append(msg.document)
            if msg.photo:
                photos.extend(msg.photo)
            if msg.caption:
                texts.append(msg.caption)
            if msg.text:
                texts.append(msg.text)
        text = "\n".join(texts).strip() if texts else None
        await self._process(update, context, base, key, docs, photos, text)

    async def _process(
        self,
        update: Update,
        context: CallbackContext,
        message: Message,
        key: str,
        documents: list | None = None,
        photos: list | None = None,
        text_override: str | None = None,
    ) -> None:
        logger.info(
            f"Processing message: from={update.effective_user.username}, "
            f"text={bool(text_override or message.text)}, "
            f"voice={bool(message.voice)}, "
            f"documents={len(documents or [])}, photos={len(photos or [])}"
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
        docs = documents or []
        photos_list = photos or []
        if (docs or photos_list) and config.files.enabled:
            if is_group and not is_reply_to_bot:
                logger.info("Ignoring file in group chat - not a reply to bot")
                return

            with FileProcessor() as file_processor:
                file_content = await file_processor.process_files(
                    documents=docs,
                    photos=photos_list,
                )

        # Получаем текст сообщения
        if text_override is not None:
            text_msg = Message(
                message_id=message.message_id,
                date=message.date,
                chat=message.chat,
                text=text_override,
                from_user=message.from_user,
                reply_to_message=message.reply_to_message,
                entities=message.entities,
                caption_entities=message.caption_entities,
            )
            text_msg.set_bot(context.bot)
        else:
            text_msg = message

        if text_msg.chat.type == Chat.PRIVATE:
            question = await questions.extract_private(text_msg, context)
        else:
            question, text_msg = await questions.extract_group(text_msg, context)

            # Если это ответ с упоминанием бота на сообщение с файлом/голосовым
            if (
                is_bot_mentioned
                and text_msg.reply_to_message
                and (
                    text_msg.reply_to_message.voice
                    or text_msg.reply_to_message.document
                    or text_msg.reply_to_message.photo
                )
            ):
                if text_msg.reply_to_message.voice:
                    voice_file = await text_msg.reply_to_message.voice.get_file()
                    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp_file:
                        await voice_file.download_to_drive(tmp_file.name)
                        voice_path = Path(tmp_file.name)

                    voice_content = await self.voice_processor.transcribe(voice_path)
                    voice_path.unlink()

                    if voice_content:
                        file_content = voice_content
                    else:
                        await text_msg.reply_text(
                            "Sorry, I couldn't understand the voice message."
                        )
                        return

                elif text_msg.reply_to_message.document or text_msg.reply_to_message.photo:
                    with FileProcessor() as file_processor:
                        file_content = await file_processor.process_files(
                            documents=[text_msg.reply_to_message.document]
                            if text_msg.reply_to_message.document
                            else [],
                            photos=text_msg.reply_to_message.photo
                            if text_msg.reply_to_message.photo
                            else [],
                        )

        # Cancel pending "file only" prompts
        prompt_task = self._file_prompts.pop(key, None)
        if prompt_task:
            prompt_task.cancel()

        # Обработка файлового контента
        user = UserData(context.user_data)
        if file_content and not question:
            prev = user.data.get("last_file_content")
            user.data["last_file_content"] = (
                f"{prev}\n\n{file_content}" if prev else file_content
            )
            # defer prompt in case user sends more data
            self._file_prompts[key] = asyncio.create_task(
                self._delayed_file_prompt(key, message, context)
            )
            return

        if question and not file_content:
            stored = user.data.pop("last_file_content", None)
            if stored:
                file_content = stored
                question = f"{question}\n\n{stored}"
        elif file_content:
            stored = user.data.pop("last_file_content", None)
            if stored:
                file_content = f"{stored}\n\n{file_content}"

        if not file_content and not question:
            logger.info("No content extracted, ignoring message")
            return

        if file_content:
            question = f"{question}\n\n{file_content}" if question else file_content

        await self.reply_func(update=update, message=message, context=context, question=question)

    async def _delayed_file_prompt(
        self, key: str, message: Message, context: CallbackContext
    ) -> None:
        try:
            await asyncio.sleep(self.debounce_sec)
        except asyncio.CancelledError:
            return
        if self._buffers.get(key) or key in self._tasks:
            return
        user = UserData(context.user_data)
        if user.data.get("last_file_content"):
            await message.reply_text("This is a file. What should I do with it?")
        self._file_prompts.pop(key, None)

