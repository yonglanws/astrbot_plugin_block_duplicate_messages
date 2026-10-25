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

# ---- 跨平台去重（v2.1） ----
# 默认参与跨平台去重的 QQ 系平台类型名（同一 AstrBot 实例内可共处同一群聊的平台）
DEFAULT_CROSS_PLATFORM_PLATFORMS = "aiocqhttp,qq_official,qq_official_webhook"
DEFAULT_CROSS_PLATFORM_TIME_WINDOW = 5.0
MIN_CROSS_PLATFORM_TIME_WINDOW = 1.0
MAX_CROSS_PLATFORM_TIME_WINDOW = 60.0
# 群名解析缓存：成功 1 小时，失败 5 分钟
GROUP_NAME_CACHE_TTL = 3600.0
GROUP_NAME_FAILURE_TTL = 300.0
GROUP_NAME_RESOLVE_TIMEOUT = 3.0
# 指纹缓存每 N 次操作做一次全量清扫
SWEEP_INTERVAL_OPS = 500