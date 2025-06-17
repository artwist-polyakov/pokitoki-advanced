"""Telegram chat bot built using the language model from OpenAI."""

import logging
import sys
import textwrap
import time

from telegram import Chat, Message, Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackContext,
    CommandHandler,
    MessageHandler,
    PicklePersistence,
)
from telegram.ext import filters as tg_filters

from bot import askers, commands, models, questions
from bot.config import config
from bot.fetcher import Fetcher
from bot.filters import Filters
from bot.models import ChatData, UserData
from bot.voice import VoiceProcessor
from bot.batching import BatchProcessor

logging.basicConfig(
    stream=sys.stdout,
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)
logging.getLogger("bot").setLevel(logging.INFO)
logging.getLogger("bot.ai.chat").setLevel(logging.INFO)
logging.getLogger("bot.commands").setLevel(logging.INFO)
logging.getLogger("bot.questions").setLevel(logging.INFO)
logging.getLogger("__main__").setLevel(logging.INFO)

logger = logging.getLogger(__name__)

# retrieves remote content
fetcher = Fetcher()

# telegram message filters
filters = Filters()

voice_processor = VoiceProcessor()


def main():
    persistence = PicklePersistence(filepath=config.persistence_path)
    application = (
        ApplicationBuilder()
        .token(config.telegram.token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .persistence(persistence)
        .concurrent_updates(True)
        .get_updates_http_version("1.1")
        .http_version("1.1")
        .build()
    )
    add_handlers(application)
    application.run_polling()


def add_handlers(application: Application):
    """Adds command handlers."""

    # info commands
    application.add_handler(CommandHandler("start", commands.Start()))
    application.add_handler(
        CommandHandler("help", commands.Help(), filters=filters.users)
    )
    application.add_handler(
        CommandHandler("version", commands.Version(), filters=filters.users)
    )

    # admin commands
    application.add_handler(
        CommandHandler(
            "config", commands.Config(filters), filters=filters.admins_private
        )
    )
    application.add_handler(
        CommandHandler(
            "model", commands.Model(), filters=filters.users_or_chats
        )
    )

    # message-related commands
    application.add_handler(
        CommandHandler(
            "imagine", commands.Imagine(reply_to), filters=filters.users_or_chats
        )
    )
    application.add_handler(
        CommandHandler("prompt", commands.Prompt(), filters=filters.users)
    )
    application.add_handler(
        CommandHandler(
            "retry", commands.Retry(reply_to), filters=filters.users_or_chats
        )
    )

    async def message_handler(update: Update, context: CallbackContext) -> None:
        message = update.message or update.edited_message
        if message.chat.type != Chat.PRIVATE:
            bot_username = context.bot.username
            is_mentioned = filters.is_bot_mentioned(message, bot_username)
            is_reply = filters.is_reply_to_bot(message, bot_username)
            if not (is_mentioned or is_reply) and update.effective_user.id not in batch_processor.batches:
                return
        await batch_processor.add_message(
            update=update,
            message=message,
            context=context,
        )

    # text message handler
    application.add_handler(
        MessageHandler(
            (
                    filters.text_filter
                    | tg_filters.PHOTO
                    | tg_filters.Document.ALL
                    | tg_filters.VOICE
            )
            & ~tg_filters.COMMAND
            & filters.users_or_chats,
            message_handler,
        )
    )

    # generic error handler
    application.add_error_handler(commands.Error())


async def post_init(application: Application) -> None:
    """Defines bot settings."""
    bot = application.bot
    logging.info(f"config: file={config.filename}, version={config.version}")
    logging.info(f"allowed users: {config.telegram.usernames}")
    logging.info(f"allowed chats: {config.telegram.chat_ids}")
    logging.info(f"admins: {config.telegram.admins}")
    logging.info(f"model name: {config.openai.model}")
    logging.info(f"bot: username={bot.username}, id={bot.id}")
    logging.info(
        f"voice processing: enabled={config.voice.enabled}, "
        f"tts_enabled={config.voice.tts_enabled}, "
        f"language={config.voice.language}"
    )
    await bot.set_my_commands(commands.BOT_COMMANDS)


async def post_shutdown(application: Application) -> None:
    """Frees acquired resources."""
    await fetcher.close()


def with_message_limit(func):
    """Refuses to reply if the user has exceeded the message limit."""

    async def wrapper(
            update: Update,
            message: Message,
            context: CallbackContext,
            question: str,
            **kwargs,
    ) -> None:
        username = update.effective_user.username
        user = UserData(context.user_data)

        # check if the message counter exceeds the message limit
        if (
                not filters.is_known_user(username)
                and user.message_counter.value
                >= config.conversation.message_limit.count
                > 0
                and not user.message_counter.is_expired()
        ):
            # this is a group user and they have exceeded the message limit
            wait_for = models.format_timedelta(user.message_counter.expires_after())
            await message.reply_text(
                f"Please wait {wait_for} before asking a new question."
            )
            return

        # this is a known user or they have not exceeded the message limit,
        # so proceed to the actual message handler
        await func(
            update=update,
            message=message,
            context=context,
            question=question,
            **kwargs,
        )

        # increment the message counter
        message_count = user.message_counter.increment()
        logger.debug(f"user={username}, n_messages={message_count}")

    return wrapper


# In bot/bot.py

@with_message_limit
async def reply_to(
        update: Update,
        message: Message,
        context: CallbackContext,
        question: str,
        send_voice_reply: bool = False,
) -> None:
    """Replies to a prepared question."""
    logger.info(
        f"Reply_to called for user={update.effective_user.username} with prepared prompt."
    )

    if not question:
        logger.warning("Prompt is empty, skipping reply.")
        return

    await message.chat.send_action(
        action="typing", message_thread_id=message.message_thread_id
    )

    try:
        chat = ChatData(context.chat_data)
        model_name = chat.model or config.openai.model
        asker = askers.create(model_name, question)

        user_id = message.from_user.username or message.from_user.id
        logger.info(
            f"-> question id={message.id}, user={user_id}, n_chars={len(question)}"
        )

        prepared_question, is_follow_up = questions.prepare(question)
        prepared_question = await fetcher.substitute_urls(prepared_question)

        # The logic for `last_file_content` has been removed.
        # The batcher now handles combining files and text.
        user = UserData(context.user_data)

        if message.chat.type == Chat.PRIVATE:
            if is_follow_up:
                history = user.messages.as_list()
            else:
                user.messages.clear()
                history = []
        else:
            prev_message = questions.extract_prev(message, context)
            history = [("", prev_message)] if prev_message else []

        start = time.perf_counter_ns()
        answer = await asker.ask(
            prompt=chat.prompt,
            question=prepared_question,
            history=history,
        )
        elapsed = int((time.perf_counter_ns() - start) / 1e6)

        logger.info(
            f"<- answer id={message.id}, user={user_id}, "
            f"n_chars={len(answer)}, len_history={len(history)}, took={elapsed}ms"
        )

        user.messages.add(question, answer)
        await asker.reply(message, context, answer)

        if send_voice_reply and config.voice.tts_enabled:
            speech_file = await voice_processor.text_to_speech(answer)
            if speech_file:
                try:
                    with open(speech_file, "rb") as audio:
                        await message.reply_voice(audio)
                finally:
                    speech_file.unlink()

    except Exception as exc:
        class_name = f"{exc.__class__.__module__}.{exc.__class__.__qualname__}"
        error_text = f"{class_name}: {exc}"
        logger.error("Failed to answer: %s", error_text)
        text = textwrap.shorten(f"⚠️ {error_text}", width=255, placeholder="...")
        await message.reply_text(text)


# Batch processor instance created after reply function is defined
batch_processor = BatchProcessor(
    reply_to, buffer_time=config.conversation.batching_buffer_time
)

if __name__ == "__main__":
    main()
