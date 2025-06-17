import datetime as dt
import unittest

from telegram import Chat, Message, MessageEntity, Update, User
from telegram.constants import ChatType
from telegram.ext import CallbackContext

from bot import askers, bot
from bot.batching import BatchProcessor
from bot.filters import Filters
from bot.config import config
from tests.mocks import FakeApplication, FakeBot, FakeGPT
from tests.test_commands import Helper


class BatchMessageGroupTest(unittest.IsolatedAsyncioTestCase, Helper):
    def setUp(self):
        askers.TextAsker.model_factory = lambda name: FakeGPT()
        self.bot = FakeBot("bot")
        self.chat = Chat(id=1, type=ChatType.GROUP)
        self.chat.set_bot(self.bot)
        self.application = FakeApplication(self.bot)
        self.application.user_data[1] = {}
        self.context = CallbackContext(self.application, chat_id=1, user_id=1)
        self.user = User(id=1, first_name="Alice", is_bot=False, username="alice")
        config.telegram.usernames = ["alice"]
        self.processor = BatchProcessor(bot.reply_to, buffer_time=0)
        self.filters = Filters()

    async def test_no_mention_batch(self):
        update = self._create_update(11, text="What is your name?")
        await self.processor.add_message(update, update.message, self.context)
        token = self.processor.tokens[update.effective_user.id]
        await self.processor._finalize_batch(update.effective_user.id, token)
        self.assertEqual(self.bot.text, "")

