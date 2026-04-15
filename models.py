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
            message_similarity_threshold=int(config.get("message_similarity_threshold", 0))
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
        
        return True


@dataclass
class BotInfo:
    """Bot信息"""
    bot_id: str
    bot_name: str
    priority: int = 0
    last_selected_time: float = 0.0
    selection_count: int = 0