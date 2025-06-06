"""/model command."""

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import CallbackContext

from bot.config import config
from bot.models import ChatData

HELP_MESSAGE = """Syntax:
<code>/model [model name]</code>

To use the default model:
<code>/model reset</code>"""

RESET = "reset"


class ModelCommand:
    """Sets a custom chat model."""

    async def __call__(self, update: Update, context: CallbackContext) -> None:
        message = update.message or update.edited_message

        if update.effective_user.username not in config.telegram.admins:
            # Only admins are allowed to change the model
            await message.reply_text(
                "You don't have permission to change the model.",
                parse_mode=ParseMode.MARKDOWN,
            )

            return

        chat = ChatData(context.chat_data)
        _, _, model = message.text.partition(" ")
        if not model:
            if chat.model:
                await message.reply_text(
                    f"Using custom model:\n<code>{chat.model}</code>",
                    parse_mode=ParseMode.HTML,
                )
                return
            await message.reply_text(HELP_MESSAGE, parse_mode=ParseMode.HTML)
            return

        if model == RESET:
            chat.model = ""
            await message.reply_text(
                f"✓ Using default model:\n<code>{config.openai.model}</code>",
                parse_mode=ParseMode.HTML,
            )
            return

        chat.model = model
        await message.reply_text(
            f"✓ Set custom model:\n<code>{model}</code>",
            parse_mode=ParseMode.HTML,
        )
