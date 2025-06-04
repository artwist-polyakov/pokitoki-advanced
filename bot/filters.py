"""Bot message filters."""

import logging
from dataclasses import dataclass
from typing import Union

from telegram.ext import filters
from telegram import Message, MessageEntity

from bot.config import config

logger = logging.getLogger(__name__)


@dataclass
class Filters:
    """Filters for the incoming Telegram messages."""

    users: Union[filters.MessageFilter, filters.User]
    admins: filters.User
    chats: Union[filters.MessageFilter, filters.Chat]

    users_or_chats: filters.BaseFilter
    admins_private: filters.BaseFilter
    text_filter: filters.BaseFilter

    def __init__(self) -> None:
        """Defines users and chats that are allowed to use the bot."""
        if config.telegram.usernames:
            self.users = filters.User(username=config.telegram.usernames)
            # Convert all chat IDs to negative for groups
            chat_ids = [-abs(chat_id) for chat_id in config.telegram.chat_ids]
            self.chats = filters.Chat(chat_id=chat_ids)
        else:
            self.users = filters.ALL
            self.chats = filters.ALL

        if config.telegram.admins:
            self.admins = filters.User(username=config.telegram.admins)
        else:
            self.admins = filters.User(username=[])

        self.users_or_chats = self.users | self.chats
        self.admins_private = self.admins & filters.ChatType.PRIVATE
        self.text_filter = filters.TEXT

        logger.info(
            f"Filters initialized: text={self.text_filter}, users_or_chats={self.users_or_chats}"
        )

    def reload(self) -> None:
        """Reloads users and chats from config."""
        if self.users == filters.ALL and config.telegram.usernames:
            # cannot update the filter from ALL to specific usernames without a restart
            raise Exception("Restart the bot for changes to take effect")
        self.users.usernames = config.telegram.usernames
        self.chats.chat_ids = config.telegram.chat_ids
        self.admins.usernames = config.telegram.admins

    def is_known_user(self, username: str) -> bool:
        """Checks if the username is included in the `users` filter."""
        if self.users == filters.ALL:
            return False
        return username in self.users.usernames

    def is_bot_mentioned(self, message: Message, bot_username: str) -> bool:
        """Checks if the bot is mentioned in the message."""
        entities = message.entities or message.caption_entities
        if not entities:
            return False

        source = message.text if message.text is not None else message.caption or ""
        for entity in entities:
            if entity.type == MessageEntity.MENTION:
                mention_text = source[entity.offset : entity.offset + entity.length]
                if mention_text.lower() == f"@{bot_username.lower()}":
                    return True
        return False

    def is_reply_to_bot(self, message: Message, bot_username: str) -> bool:
        """Checks if the message is a reply to the bot."""
        return (
            message.reply_to_message
            and message.reply_to_message.from_user
            and message.reply_to_message.from_user.username == bot_username
        )
