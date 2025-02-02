import logging
import hashlib
import uuid
import io  # Добавляем импорт io
from datetime import datetime
from typing import Dict, Callable, Awaitable
from dataclasses import dataclass
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackContext, CallbackQueryHandler
from telegram.constants import ParseMode
import re
from telegram.helpers import create_deep_linked_url

from bot.models import ParsingState, ParsedMessage

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # Устанавливаем уровень логирования в DEBUG


@dataclass
class ParseSession:
    """Сессия парсинга для пользователя/чата."""

    state: ParsingState
    cleanup_callback: Callable[[], Awaitable[None]]
    message_id: int
    callback_id: str


# Глобальный словарь для хранения активных сессий
active_sessions: Dict[int, ParseSession] = {}


class ParseManyCommand:
    """Handles the /parsemany command and related functionality."""

    def __init__(self):
        logger.debug("ParseManyCommand initialized")
        pass

    async def cleanup_all_sessions(self):
        for chat_id in list(active_sessions.keys()):
            try:
                session = active_sessions[chat_id]
                await session.cleanup_callback()
                del active_sessions[chat_id]
            except Exception as e:
                logger.error(f"Cleanup error: {str(e)}")
            active_sessions.clear()

    async def __call__(self, update: Update, context: CallbackContext) -> None:
        chat_id = update.effective_chat.id
        user_id = update.effective_user.id

        if chat_id in active_sessions:
            await update.message.reply_text(
                "You already have an active parsing session.\n"
                "Use /parseit to generate document or wait for previous session to finish."
            )
            return

        # Создаем состояние и сессию
        state = ParsingState()
        state.user_id = user_id

        # Создаем уникальный session_id
        session_id = uuid.uuid4().hex[:8]

        # Отправляем сообщение с инструкцией
        message = await update.message.reply_text(
            f"Started new parsing session (ID: {session_id}).\n\n"
            "Now send me messages, files, voice messages - I'll collect them all.\n"
            f"When you're done, use command:\n/parseit {session_id}"
        )

        # Создаем функцию очистки
        async def cleanup():
            try:
                await context.bot.delete_message(chat_id, message.message_id)
            except Exception as e:
                logger.error(f"Failed to delete message: {e}")

        session = ParseSession(
            state=state,
            cleanup_callback=cleanup,
            message_id=message.message_id,
            callback_id=session_id,
        )

        active_sessions[chat_id] = session
        logger.debug(f"Created session {session_id} for chat {chat_id}")

    async def parse_session(self, update: Update, context: CallbackContext) -> None:
        """Handles the /parseit command."""
        chat_id = update.effective_chat.id

        # Получаем session_id из аргументов команды
        if not context.args:
            await update.message.reply_text(
                "Please provide session ID.\nExample: /parseit abc123"
            )
            return

        session_id = context.args[0]
        logger.debug(f"Parsing requested for session {session_id}")

        session = active_sessions.get(chat_id)
        if not session:
            await update.message.reply_text(
                "No active parsing session.\nUse /parsemany to start new session."
            )
            return

        if session.callback_id != session_id:
            await update.message.reply_text(
                f"Invalid session ID. Current session ID: {session.callback_id}"
            )
            return

        try:
            # Проверяем незавершенные процессы
            if session.state.pending_hashes:
                await update.message.reply_text(
                    "Some files are still processing. Please wait and try again."
                )
                return

            # Проверяем наличие контента
            if not (
                session.state.messages
                or session.state.voice_messages
                or session.state.file_contents
            ):
                await update.message.reply_text(
                    "No content to parse. Please send some messages first."
                )
                return

            # Проверяем, все ли файлы обработаны
            for msg in session.state.messages:
                if msg.attached_files:
                    for filename in msg.attached_files:
                        if filename not in session.state.file_contents:
                            await update.message.reply_text(
                                f"Still processing file: {filename}. Please wait and try again."
                            )
                            return

            # Генерируем документ
            document_content = self._generate_document(session.state)
            if not document_content:
                await update.message.reply_text("Failed to generate document")
                return

            # Отправляем документ
            document = io.BytesIO(document_content.encode("utf-8"))
            document.name = "parsed_content.txt"

            await update.message.reply_document(
                document=document,
                filename="parsed_content.txt",
                caption=f"Parsed content from session {session_id}",
            )

            # Очищаем сессию и все связанные данные
            await session.cleanup_callback()
            if chat_id in active_sessions:
                del active_sessions[chat_id]

        except Exception as e:
            logger.error(f"Parse error: {str(e)}", exc_info=True)
            # В случае ошибки тоже очищаем
            if chat_id in active_sessions:
                await active_sessions[chat_id].cleanup_callback()
                del active_sessions[chat_id]
            await update.message.reply_text(f"Error generating document: {str(e)}")

    def _generate_document(self, state: ParsingState) -> str:
        content = []

        # Проверяем, есть ли что парсить
        if not (state.messages or state.voice_messages or state.file_contents):
            logger.warning("No content to parse: state is empty")
            return ""

        logger.info(
            f"Generating document with: "
            f"{len(state.messages)} messages, "
            f"{len(state.voice_messages)} voice messages, "
            f"{len(state.file_contents)} files"
        )

        # Отладочный вывод содержимого state
        for msg in state.messages:
            logger.info(f"Message content: {msg.content[:50]}...")
        for msg in state.voice_messages:
            logger.info(f"Voice message content: {msg.content[:50]}...")
        for filename in state.file_contents:
            logger.info(f"File: {filename}")

        logger.info("Starting document generation...")
        content.append(
            "Generated document containing parsed messages, files and media\n"
        )

        # Добавляем текстовые сообщения
        if state.messages:
            logger.info(f"Adding {len(state.messages)} text messages")
            content.append("[[[MESSAGES--BEGIN]]]")
            for msg in state.messages:
                content.append(
                    f"From: {msg.sender_id}\n"
                    f"Time: {msg.timestamp.strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"Content: {msg.content}"
                )
                if msg.attached_files:
                    content.append(f"Attached files: {', '.join(msg.attached_files)}")
                content.append("-" * 40)
            content.append("[[[MESSAGES--END]]]\n")

        # Добавляем голосовые сообщения
        if state.voice_messages:
            logger.info(f"Adding {len(state.voice_messages)} voice messages")
            content.append("[[[VOICE_MESSAGES--BEGIN]]]")
            for msg in state.voice_messages:
                content.append(
                    f"From: {msg.sender_id}\n"
                    f"Time: {msg.timestamp.strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"Transcription: {msg.content}"
                )
                content.append("-" * 40)
            content.append("[[[VOICE_MESSAGES--END]]]\n")

        # Добавляем содержимое файлов
        if state.file_contents:
            logger.info(f"Adding {len(state.file_contents)} files")
            for filename, file_content in state.file_contents.items():
                # Удаляем теги файлов
                cleaned_content = file_content.replace(f"<file_{filename}>", "")
                cleaned_content = cleaned_content.replace(f"</file_{filename}>", "")

                # Не добавляем .txt к именам файлов
                content.append(f"[[[{filename}--BEGIN]]]")
                content.append(cleaned_content)
                content.append(f"[[[{filename}--END]]]\n")

        final_content = "\n".join(content)
        logger.info(f"Generated document with size: {len(final_content)} bytes")
        logger.debug(f"Document content preview: {final_content[:200]}...")

        return final_content


def get_content_hash(content: str) -> str:
    """Generates a hash for content tracking."""
    return hashlib.md5(content.encode()).hexdigest()
