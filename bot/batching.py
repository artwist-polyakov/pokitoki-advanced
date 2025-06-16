# Batch processing of incoming Telegram messages

import asyncio
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

from telegram import Message, Update
from telegram.ext import CallbackContext

from bot.file_processor import FileProcessor
from bot.voice import VoiceProcessor

voice_processor = VoiceProcessor()
file_processor = FileProcessor()


class IncomingMessage:
    """Base class for a single incoming Telegram message."""

    def __init__(self, message: Message) -> None:
        self.message = message
        self.content: str = ""
        self.has_text = bool(message.text or message.caption or message.voice)

    async def process(self) -> None:
        raise NotImplementedError


class MarkItDownMessage(IncomingMessage):
    """Processes documents, images and voice messages."""

    def __init__(self, message: Message, file_proc: FileProcessor) -> None:
        super().__init__(message)
        self.file_processor = file_proc

    async def process(self) -> None:
        content_parts = []

        if self.message.document or self.message.photo:
            file_content = await self.file_processor.process_files(
                documents=[self.message.document] if self.message.document else [],
                photos=self.message.photo if self.message.photo else [],
            )
            if file_content:
                content_parts.append(file_content)

        if self.message.voice:
            voice_file = await self.message.voice.get_file()
            with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp_file:
                await voice_file.download_to_drive(tmp_file.name)
                voice_path = Path(tmp_file.name)
            voice_content = await voice_processor.transcribe(voice_path)
            voice_path.unlink()
            if voice_content:
                content_parts.append(voice_content)

        self.content = "\n\n".join(content_parts)


class BatchMessage:
    """Container for a group of ``IncomingMessage`` objects."""

    def __init__(self) -> None:
        self.messages: List[IncomingMessage] = []
        self.tasks: List[asyncio.Task] = []
        self.last_update: Optional[Update] = None
        self.context: Optional[CallbackContext] = None
        self.has_voice: bool = False
        self.caption: Optional[str] = None
        self.is_follow_up: bool = False

    def add(self, msg: IncomingMessage, update: Update, context: CallbackContext) -> None:
        self.messages.append(msg)
        self.last_update = update
        self.context = context
        self.tasks.append(asyncio.create_task(msg.process()))
        bot_username = context.bot.username
        if (
            msg.message.reply_to_message
            and msg.message.reply_to_message.from_user
            and msg.message.reply_to_message.from_user.username == bot_username
        ):
            self.is_follow_up = True
        if msg.message.voice:
            self.has_voice = True
        if msg.message.caption:
            if self.caption:
                self.caption = f"{self.caption}\n{msg.message.caption}"
            else:
                self.caption = msg.message.caption
        elif msg.message.text:
            if self.caption:
                self.caption = f"{self.caption}\n{msg.message.text}"
            else:
                self.caption = msg.message.text

    def is_ready(self) -> bool:
        return all(t.done() for t in self.tasks)

    async def wait_until_ready(self) -> None:
        """Waits until all processing tasks are finished."""
        while True:
            pending = [t for t in self.tasks if not t.done()]
            if not pending:
                break
            await asyncio.gather(*pending, return_exceptions=True)

    async def get_full_prompt(self) -> str:
        if self.tasks:
            await self.wait_until_ready()

        content_parts = [m.content for m in self.messages if m.content]
        full_content = "\n\n".join(content_parts)

        final_parts = []
        if self.caption:
            final_parts.append(self.caption)
        if full_content:
            final_parts.append(full_content)

        full_prompt = "\n\n".join(final_parts)
        if self.is_follow_up:
            return f"+ {full_prompt}"
        return full_prompt

    @property
    def last_message(self) -> Optional[Message]:
        if self.last_update:
            return self.last_update.message or self.last_update.edited_message
        return None


class BatchProcessor:
    """Collects messages for a user and sends them as a single request."""

    def __init__(self, reply_func, buffer_time: float = 1.5) -> None:
        self.reply_func = reply_func
        self.buffer_time = buffer_time
        self.batches: Dict[int, BatchMessage] = {}
        self.timers: Dict[int, asyncio.TimerHandle] = {}
        self.tokens: Dict[int, int] = {}

    async def add_message(
        self,
        update: Update,
        message: Message,
        context: CallbackContext,
    ) -> None:
        user_id = update.effective_user.id
        batch = self.batches.get(user_id)
        if not batch:
            batch = BatchMessage()
            self.batches[user_id] = batch

        incoming = MarkItDownMessage(message, file_proc=file_processor)
        batch.add(incoming, update, context)

        if user_id in self.timers:
            self.timers[user_id].cancel()
        token = self.tokens.get(user_id, 0) + 1
        self.tokens[user_id] = token
        loop = asyncio.get_running_loop()
        self.timers[user_id] = loop.call_later(
            self.buffer_time,
            lambda tok=token: asyncio.create_task(self._finalize_batch(user_id, tok)),
        )

    async def _finalize_batch(self, user_id: int, token: int) -> None:
        if self.tokens.get(user_id) != token:
            # A newer batch timer exists, so skip finalizing
            return
        batch = self.batches.get(user_id)
        if not batch:
            return
        prompt = await batch.get_full_prompt()
        has_user_text = any(m.has_text for m in batch.messages)
        update = batch.last_update
        context = batch.context
        message = batch.last_message
        if update and context and message:
            if not has_user_text:
                from bot.models import UserData

                user = UserData(context.user_data)
                if prompt:
                    user.data["last_file_content"] = prompt
                await message.reply_text("This is a file. What should I do with it?")
            else:
                await self.reply_func(
                    update=update,
                    message=message,
                    context=context,
                    question=prompt,
                    send_voice_reply=batch.has_voice,
                )
        timer = self.timers.pop(user_id, None)
        if timer:
            timer.cancel()
        self.batches.pop(user_id, None)
        self.tokens.pop(user_id, None)
