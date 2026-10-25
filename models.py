"""数据模型模块"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Dict, Any
from enum import Enum


class CommandStatus(Enum):
    """指令状态枚举"""
    PENDING = "pending"
    EXECUTING = "executing"
    COMPLETED = "completed"
    BLOCKED = "blocked"


@dataclass
class CommandRecord:
    """指令记录"""
    command: str
    group_id: str
    sender_id: str
    bot_id: str
    timestamp: float
    status: CommandStatus = CommandStatus.PENDING
    event_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "command": self.command,
            "group_id": self.group_id,
            "sender_id": self.sender_id,
            "bot_id": self.bot_id,
            "timestamp": self.timestamp,
            "status": self.status.value,
            "event_id": self.event_id
        }


@dataclass
class ConflictEvent:
    """冲突事件记录"""
    command: str
    group_id: str
    timestamp: float
    selected_bot_id: str
    blocked_bots: List[str] = field(default_factory=list)
    total_bots: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "command": self.command,
            "group_id": self.group_id,
            "timestamp": self.timestamp,
            "selected_bot_id": self.selected_bot_id,
            "blocked_bots": self.blocked_bots,
            "total_bots": self.total_bots,
            "datetime": datetime.fromtimestamp(self.timestamp).isoformat()
        }


@dataclass
class MessageInfo:
    """从消息事件中提取的去重判定所需信息

    跨平台去重的关键在于：不同平台对同一群/用户的标识完全不同
    （OneBot v11 是数字群号和 QQ 号，QQ 官方 API 是 group_openid
    和 member_openid，且 openid 每个官方 App 各不相同），
    因此跨平台匹配只能依赖消息内容指纹，同平台匹配才用精确 ID。
    """
    platform_name: str = ""   # 平台类型名，如 aiocqhttp / qq_official
    platform_id: str = ""     # 平台实例 ID（同类型多实例时用于区分）
    self_id: str = ""         # 机器人自身 ID
    group_id: str = ""        # 群 ID（跨平台语义不同，仅同平台可比）
    group_name: str = ""      # 群名（可能为空，跨平台群名锚定用）
    sender_id: str = ""       # 发送者 ID（跨平台语义不同）
    message_str: str = ""     # 原始消息文本（含 [JSON] 前缀等）
    command: str = ""         # extract_command 提取的指令标识
    woken: bool = True        # 本条消息是否会唤醒机器人响应
                              # （event.is_at_or_wake_command，弱/强记录分级用）


@dataclass
class FingerprintRecord:
    """跨平台内容指纹记录

    woken 分级说明：
    - 强记录（woken=True）：该消息唤醒了机器人，机器人会真正响应。
      强记录可以拦截后续任何匹配事件。
    - 弱记录（woken=False）：该消息不会唤醒机器人（如 @ 的是别的 bot），
      机器人自身不会响应，但某些监听型插件仍可能响应。
      弱记录只拦截同样不会唤醒的后续事件，避免误杀真正被 @ 的机器人的响应。
    """
    platform_name: str
    platform_id: str
    self_id: str
    group_id: str
    group_name: str
    sender_id: str
    fingerprint: str
    woken: bool
    timestamp: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "platform": self.platform_name,
            "group_id": self.group_id,
            "group_name": self.group_name,
            "sender_id": self.sender_id,
            "woken": self.woken,
            "timestamp": self.timestamp,
        }


@dataclass
class PluginConfig:
    """插件配置模型"""
    time_window: float = 2.0
    bot_selection_strategy: str = "round_robin"
    enable_logging: bool = True
    log_level: str = "INFO"
    max_cache_size: int = 1000
    cleanup_interval: int = 300
    block_mode: str = "all"  # command=仅指令, all=所有消息
    message_similarity_threshold: int = 0
    # ---- 跨平台去重（v2.1 新增） ----
    cross_platform_dedup: bool = True
    cross_platform_time_window: float = 5.0
    cross_platform_platforms: str = "aiocqhttp,qq_official,qq_official_webhook"
    platform_priority: str = ""
    decision_delay_ms: int = 0
    use_group_name_anchor: bool = True

    @classmethod
    def from_dict(cls, config: Dict[str, Any]) -> "PluginConfig":
        """从字典创建配置实例"""
        return cls(
            time_window=float(config.get("time_window", 2.0)),
            bot_selection_strategy=config.get("bot_selection_strategy", "round_robin"),
            enable_logging=config.get("enable_logging", True),
            log_level=config.get("log_level", "INFO"),
            max_cache_size=int(config.get("max_cache_size", 1000)),
            cleanup_interval=int(config.get("cleanup_interval", 300)),
            block_mode=config.get("block_mode", "all"),
            message_similarity_threshold=int(config.get("message_similarity_threshold", 0)),
            cross_platform_dedup=bool(config.get("cross_platform_dedup", True)),
            cross_platform_time_window=float(config.get("cross_platform_time_window", 5.0)),
            cross_platform_platforms=str(config.get(
                "cross_platform_platforms",
                "aiocqhttp,qq_official,qq_official_webhook",
            )),
            platform_priority=str(config.get("platform_priority", "")),
            decision_delay_ms=int(config.get("decision_delay_ms", 0)),
            use_group_name_anchor=bool(config.get("use_group_name_anchor", True)),
        )

    def validate(self) -> bool:
        """验证配置有效性"""
        if self.time_window < 0.5 or self.time_window > 10.0:
            raise ValueError(f"时间窗口必须在 0.5-10.0 秒之间，当前值: {self.time_window}")

        valid_strategies = ["random", "round_robin", "priority"]
        if self.bot_selection_strategy not in valid_strategies:
            raise ValueError(f"无效的Bot选择策略: {self.bot_selection_strategy}，有效值: {valid_strategies}")

        valid_modes = ["command", "all"]
        if self.block_mode not in valid_modes:
            raise ValueError(f"无效的屏蔽模式: {self.block_mode}，有效值: {valid_modes}")

        if self.max_cache_size < 100 or self.max_cache_size > 10000:
            raise ValueError(f"缓存大小必须在 100-10000 之间，当前值: {self.max_cache_size}")

        if self.cross_platform_time_window < 1.0 or self.cross_platform_time_window > 60.0:
            raise ValueError(
                f"跨平台时间窗口必须在 1.0-60.0 秒之间，当前值: {self.cross_platform_time_window}"
            )

        if self.decision_delay_ms < 0 or self.decision_delay_ms > 5000:
            raise ValueError(f"决策延迟必须在 0-5000 毫秒之间，当前值: {self.decision_delay_ms}")

        if self.cross_platform_dedup and not self.cross_platform_platforms.strip():
            raise ValueError("启用跨平台去重时，跨平台白名单不能为空")

        return True


@dataclass
class BotInfo:
    """Bot信息"""
    bot_id: str
    bot_name: str
    priority: int = 0
    last_selected_time: float = 0.0
    selection_count: int = 0
