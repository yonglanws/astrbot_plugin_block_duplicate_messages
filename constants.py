"""常量定义模块"""

import re
from typing import Pattern
from enum import Enum


class BotSelectionStrategy(Enum):
    """Bot选择策略枚举"""
    RANDOM = "random"
    ROUND_ROBIN = "round_robin"
    PRIORITY = "priority"


class MessageEmoji:
    """消息表情符号"""
    ERROR = "❌"
    SUCCESS = "✅"
    WARNING = "⚠️"
    INFO = "ℹ️"
    BLOCKED = "🚫"
    SELECTED = "⭐"


DEFAULT_TIME_WINDOW = 2.0
DEFAULT_MAX_CACHE_SIZE = 1000
DEFAULT_CLEANUP_INTERVAL = 300
DEFAULT_BOT_SELECTION_STRATEGY = BotSelectionStrategy.ROUND_ROBIN
MIN_TIME_WINDOW = 0.5
MAX_TIME_WINDOW = 10.0
COMMAND_PATTERN: Pattern = re.compile(r'^/(\S+)')
GROUP_ID_KEY_PREFIX = "group_"
COMMAND_CACHE_TTL = 10.0