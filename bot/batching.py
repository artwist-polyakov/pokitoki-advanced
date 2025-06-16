# Batch processing of incoming Telegram messages

import asyncio
from typing import Dict, List, Optional

from telegram import Message, Update
from telegram.ext import CallbackContext

from bot.file_processor import FileProcessor


class IncomingMessage:
    """Base class for a single incoming Telegram message."""

    def __init__(self, message: Message, text: str | None = None) -> None:
        self.message = message
        self.text = text
        self.content: str = ""
        self.has_text = bool(text or message.text or message.caption)

    async def process(self) -> None:
        raise NotImplementedError


class MarkItDownMessage(IncomingMessage):
    """Processes text, documents and images using FileProcessor."""

    async def process(self) -> None:
        text = self.text if self.text is not None else (self.message.text or self.message.caption or "")
        file_content: Optional[str] = None
        if (self.message.document or self.message.photo):
            with FileProcessor() as file_processor:
                file_content = await file_processor.process_files(
                    documents=[self.message.document] if self.message.document else [],
                    photos=self.message.photo if self.message.photo else [],
                )
        if file_content:
            if text:
                text = f"{text}\n\n{file_content}"
            else:
                text = file_content
        self.content = text


class BatchMessage:
    """Container for a group of ``IncomingMessage`` objects."""

    def __init__(self) -> None:
        self.messages: List[IncomingMessage] = []
        self.tasks: List[asyncio.Task] = []
        self.last_update: Optional[Update] = None
        self.context: Optional[CallbackContext] = None

    def add(self, msg: IncomingMessage, update: Update, context: CallbackContext) -> None:
        self.messages.append(msg)
        self.last_update = update
        self.context = context
        self.tasks.append(asyncio.create_task(msg.process()))

    def is_ready(self) -> bool:
        return all(t.done() for t in self.tasks)

    async def get_full_prompt(self) -> str:
        if self.tasks:
            await asyncio.gather(*self.tasks)
        parts = [m.content for m in self.messages if m.content]
        return "\n\n".join(parts)

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

    async def add_message(
        self,
        update: Update,
        message: Message,
        context: CallbackContext,
        question: str | None = None,
    ) -> None:
        user_id = update.effective_user.id
        batch = self.batches.get(user_id)
        if not batch:
            batch = BatchMessage()
            self.batches[user_id] = batch

        incoming = MarkItDownMessage(message, text=question)
        batch.add(incoming, update, context)

        if user_id in self.timers:
            self.timers[user_id].cancel()
        loop = asyncio.get_running_loop()
        self.timers[user_id] = loop.call_later(
            self.buffer_time,
            lambda: asyncio.create_task(self._finalize_batch(user_id)),
        )

    async def _finalize_batch(self, user_id: int) -> None:
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
                )
        timer = self.timers.pop(user_id, None)
        if timer:
            timer.cancel()
        self.batches.pop(user_id, None)
