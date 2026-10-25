"""工具函数模块"""

import asyncio
import functools
import time
import hashlib
import re
from typing import Callable, Any, AsyncGenerator, Optional

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from .constants import MessageEmoji, COMMAND_PATTERN


def handle_errors(func: Callable) -> Callable:
    """统一错误处理装饰器
    
    捕获并处理函数执行过程中的各种异常，向用户返回友好的错误提示。
    
    Args:
        func: 要包装的异步函数
        
    Returns:
        包装后的异步函数
    """
    @functools.wraps(func)
    async def wrapper(self, event: AstrMessageEvent, *args, **kwargs) -> AsyncGenerator[Any, None]:
        try:
            async for result in func(self, event, *args, **kwargs):
                yield result
        except PermissionError as e:
            logger.error(f"[{func.__name__}] 权限不足: {e}", exc_info=True)
            yield event.plain_result(f"{MessageEmoji.ERROR} 权限不足，请检查文件权限")
        except (IOError, OSError) as e:
            logger.error(f"[{func.__name__}] 文件操作失败: {e}", exc_info=True)
            yield event.plain_result(f"{MessageEmoji.ERROR} 文件操作失败，请检查文件是否存在")
        except asyncio.TimeoutError as e:
            logger.error(f"[{func.__name__}] 操作超时: {e}", exc_info=True)
            yield event.plain_result(f"{MessageEmoji.ERROR} 操作超时，请稍后重试")
        except ValueError as e:
            logger.warning(f"[{func.__name__}] 参数错误: {e}")
            yield event.plain_result(f"{MessageEmoji.ERROR} 参数错误: {str(e)}")
        except Exception as e:
            error_type = type(e).__name__
            logger.error(f"[{func.__name__}] 执行失败 [{error_type}]: {e}", exc_info=True)
            yield event.plain_result(f"{MessageEmoji.ERROR} 操作失败，请联系管理员")

    return wrapper


def extract_command(message: str) -> Optional[str]:
    """从消息中提取指令名或生成消息标识
    
    对于 / 开头的指令，提取指令名。
    对于包含URL的消息，提取URL作为标识。
    对于JSON消息，提取JSON内容作为标识。
    对于普通消息，生成基于消息内容的哈希标识。
    
    Args:
        message: 用户发送的消息文本
        
    Returns:
        提取到的指令名或消息标识，如果消息为空则返回None
    """
    if not message or not message.strip():
        return None
    
    message = message.strip()
    
    # 如果是 / 开头的指令，提取指令名
    if message.startswith('/'):
        match = COMMAND_PATTERN.match(message)
        if match:
            return match.group(1)
        return None
    
    # 如果是JSON消息（[JSON]前缀）
    if message.startswith('[JSON]'):
        json_content = message[6:]  # 移除[JSON]前缀
        # 尝试从JSON内容中提取URL
        url_pattern = re.compile(r'https?://[^\s<>"{}|\\^`\[\]]+')
        urls = url_pattern.findall(json_content)
        
        if urls:
            url = urls[0]
            url = url.split('?')[0].split('#')[0]
            return f"json_url_{hashlib.sha256(url.encode()).hexdigest()[:8]}"

        # 没有URL，使用整个JSON内容
        content = re.sub(r'[^\u4e00-\u9fa5a-zA-Z0-9]', '', json_content)[:50]
        if content:
            return f"json_{hashlib.sha256(content.encode()).hexdigest()[:8]}"
        return None
    
    # 尝试提取URL（http:// 或 https:// 开头）
    url_pattern = re.compile(r'https?://[^\s<>"{}|\\^`\[\]]+')
    urls = url_pattern.findall(message)
    
    if urls:
        # 如果消息中包含URL，使用第一个URL作为标识
        # 这样可以捕获相同链接的重复发送
        url = urls[0]
        # 移除URL中的跟踪参数（如抖音的分享参数）
        url = url.split('?')[0].split('#')[0]
        return f"url_{hashlib.sha256(url.encode()).hexdigest()[:8]}"
    
    # 对于普通消息，使用前50个字符作为标识（去除空格和特殊字符）
    # 保留中文、英文、数字
    content = re.sub(r'[^\u4e00-\u9fa5a-zA-Z0-9]', '', message)[:50]
    if content:
        return f"msg_{hashlib.sha256(content.encode()).hexdigest()[:8]}"
    
    return None


# 官方 API 消息中可能残留的 @ 标记格式：
# <@!xxx> / <@xxx> / <qqbot-at-user id="xxx" />
OFFICIAL_MENTION_PATTERN: re.Pattern = re.compile(
    r"<@!?[A-Za-z0-9+/=_-]+>|<qqbot-at-user[^>]*/>"
)


def normalize_content(message: str) -> str:
    """跨平台消息内容归一化

    不同平台对同一条消息的文本表示存在细微差异：
    - QQ 官方 API 可能残留 <@!xxx> 形式的 @ 标记
    - @ 消息段被剥离后可能留下多余的前导/重复空格
    - OneBot 侧每个文本段会被独立 strip，空格分布可能不同

    归一化后用于生成跨平台内容指纹。

    Args:
        message: 原始消息文本

    Returns:
        归一化后的消息文本
    """
    if not message:
        return ""
    text = OFFICIAL_MENTION_PATTERN.sub(" ", message)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def content_fingerprint(message: str) -> str:
    """生成跨平台消息内容指纹

    Args:
        message: 原始消息文本

    Returns:
        归一化内容的指纹字符串，内容为空时返回空字符串
    """
    normalized = normalize_content(message)
    if not normalized:
        return ""
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def generate_command_key(group_id: str, command: str, sender_id: str) -> str:
    """生成指令的唯一标识键
    
    Args:
        group_id: 群组ID
        command: 指令名称
        sender_id: 发送者ID
        
    Returns:
        唯一的指令键
    """
    raw_key = f"{group_id}:{command}:{sender_id}"
    return hashlib.sha256(raw_key.encode()).hexdigest()[:16]


def get_current_timestamp() -> float:
    """获取当前时间戳（秒）
    
    Returns:
        当前时间戳
    """
    return time.time()


async def call_with_timeout(coro, timeout: float = 5.0, default=None):
    """调用协程并添加超时控制
    
    Args:
        coro: 要执行的协程
        timeout: 超时时间（秒）
        default: 超时时返回的默认值
        
    Returns:
        协程执行结果或默认值
    """
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning(f"操作超时（{timeout}秒）")
        return default


def format_log_message(event_type: str, details: dict) -> str:
    """格式化日志消息
    
    Args:
        event_type: 事件类型
        details: 事件详情
        
    Returns:
        格式化后的日志消息
    """
    parts = [f"[{event_type}]"]
    for key, value in details.items():
        parts.append(f"{key}={value}")
    return " | ".join(parts)