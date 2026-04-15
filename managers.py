"""指令冲突检测管理器"""

import asyncio
import random
import hashlib
from collections import OrderedDict
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime

from astrbot.api import logger

from .constants import (
    BotSelectionStrategy,
    DEFAULT_TIME_WINDOW,
    DEFAULT_MAX_CACHE_SIZE,
    DEFAULT_CLEANUP_INTERVAL,
    COMMAND_CACHE_TTL
)
from .models import (
    CommandRecord,
    ConflictEvent,
    PluginConfig,
    BotInfo,
    CommandStatus
)
from .utils import (
    generate_command_key,
    get_current_timestamp,
    format_log_message
)


class ConflictDetectionManager:
    """指令冲突检测管理器
    
    负责检测多Bot环境下的指令冲突，并协调Bot的选择和屏蔽逻辑。
    
    核心设计：使用基于消息哈希的确定性选择策略，确保所有Bot独立做出一致决策。
    
    Attributes:
        config: 插件配置
        _command_cache: 指令缓存（OrderedDict实现LRU）
        _bot_registry: Bot注册表
        _round_robin_counter: 轮询计数器（基于时间窗口）
        _conflict_history: 冲突历史记录
        _lock: 异步锁
        _cleanup_task: 清理任务
        _current_bot_id: 当前Bot ID
    """
    
    def __init__(self, config: PluginConfig, current_bot_id: str = ""):
        """初始化管理器
        
        Args:
            config: 插件配置对象
            current_bot_id: 当前Bot的ID
        """
        self.config = config
        self._current_bot_id = current_bot_id
        
        self._command_cache: OrderedDict[str, CommandRecord] = OrderedDict()
        self._bot_registry: Dict[str, BotInfo] = {}
        self._round_robin_counter: Dict[str, int] = {}
        self._conflict_history: List[ConflictEvent] = []
        self._max_history_size = 1000
        
        self._lock = asyncio.Lock()
        self._cleanup_task = None
        self._running = False

    async def start(self):
        """启动管理器"""
        self._running = True
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info(f"指令冲突检测管理器已启动 | Bot={self._current_bot_id} | 策略={self.config.bot_selection_strategy} | 时间窗口={self.config.time_window}s")

    async def terminate(self):
        """停止管理器并清理资源"""
        self._running = False
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        logger.info(f"指令冲突检测管理器已停止 | Bot={self._current_bot_id}")

    def register_bot(self, bot_id: str, bot_name: str, priority: int = 0):
        """注册Bot信息
        
        Args:
            bot_id: Bot唯一标识
            bot_name: Bot名称
            priority: 优先级（数字越大优先级越高）
        """
        if bot_id not in self._bot_registry:
            self._bot_registry[bot_id] = BotInfo(
                bot_id=bot_id,
                bot_name=bot_name,
                priority=priority
            )
            logger.info(f"注册Bot: {bot_name} ({bot_id}) | 优先级={priority}")

    async def check_and_handle_command(
        self,
        command: str,
        group_id: str,
        sender_id: str,
        bot_id: str,
        event_id: str = ""
    ) -> Tuple[bool, Optional[ConflictEvent]]:
        """检查并处理指令冲突
        
        使用基于消息哈希的确定性选择策略，确保在多Bot环境下所有Bot
        独立做出一致的决策。
        
        Args:
            command: 指令名称
            group_id: 群组ID
            sender_id: 发送者ID
            bot_id: 当前Bot ID
            event_id: 事件ID
            
        Returns:
            元组 (是否允许执行, 冲突事件)
        """
        async with self._lock:
            current_time = get_current_timestamp()
            command_key = generate_command_key(group_id, command, sender_id)
            
            existing_record = self._command_cache.get(command_key)
            
            # 情况1：缓存中没有该指令，这是第一个Bot收到
            if existing_record is None:
                record = CommandRecord(
                    command=command,
                    group_id=group_id,
                    sender_id=sender_id,
                    bot_id=bot_id,
                    timestamp=current_time,
                    status=CommandStatus.EXECUTING,
                    event_id=event_id
                )
                await self._add_to_cache(command_key, record)
                
                if self.config.enable_logging:
                    logger.info(format_log_message("新指令", {
                        "command": command,
                        "group": group_id,
                        "sender": sender_id,
                        "bot": bot_id
                    }))
                
                # 第一个收到指令的Bot，使用确定性算法决定是否执行
                should_execute = await self._deterministic_select(
                    command_key=command_key,
                    command=command,
                    group_id=group_id,
                    sender_id=sender_id,
                    current_time=current_time,
                    bot_id=bot_id
                )
                
                if not should_execute:
                    record.status = CommandStatus.BLOCKED
                
                return should_execute, None
            
            # 情况2：缓存中有该指令，检查时间窗口
            time_diff = current_time - existing_record.timestamp
            
            # 如果时间窗口已过或指令已完成，重置状态
            if time_diff > self.config.time_window or existing_record.status == CommandStatus.COMPLETED:
                existing_record.bot_id = bot_id
                existing_record.timestamp = current_time
                existing_record.status = CommandStatus.EXECUTING
                existing_record.event_id = event_id
                
                self._command_cache.move_to_end(command_key)
                
                # 重新使用确定性算法选择
                should_execute = await self._deterministic_select(
                    command_key=command_key,
                    command=command,
                    group_id=group_id,
                    sender_id=sender_id,
                    current_time=current_time,
                    bot_id=bot_id
                )
                
                if not should_execute:
                    existing_record.status = CommandStatus.BLOCKED
                
                return should_execute, None
            
            # 情况3：在时间窗口内，且指令正在执行中，需要处理冲突
            conflict_event = await self._handle_conflict(
                command_key=command_key,
                existing_record=existing_record,
                new_bot_id=bot_id,
                current_time=current_time
            )
            
            should_execute = (conflict_event and conflict_event.selected_bot_id == bot_id)
            
            if not should_execute:
                blocked_record = CommandRecord(
                    command=command,
                    group_id=group_id,
                    sender_id=sender_id,
                    bot_id=bot_id,
                    timestamp=current_time,
                    status=CommandStatus.BLOCKED,
                    event_id=event_id
                )
                await self._add_to_cache(f"{command_key}_blocked_{bot_id}", blocked_record)
            
            return should_execute, conflict_event

    async def _deterministic_select(
        self,
        command_key: str,
        command: str,
        group_id: str,
        sender_id: str,
        current_time: float,
        bot_id: str
    ) -> bool:
        """基于哈希的确定性选择
        
        使用消息内容的哈希值来确定性选择是否执行，确保在多Bot环境下
        所有Bot独立做出一致的决策。
        
        Args:
            command_key: 指令键
            command: 指令名称
            group_id: 群组ID
            sender_id: 发送者ID
            current_time: 当前时间戳
            bot_id: 当前Bot ID
            
        Returns:
            是否允许执行
        """
        strategy = BotSelectionStrategy(self.config.bot_selection_strategy)
        
        if strategy == BotSelectionStrategy.RANDOM:
            # 基于消息哈希的伪随机选择
            # 使用当前时间窗口（秒级）作为种子的一部分
            time_seed = int(current_time / self.config.time_window)
            hash_input = f"{command_key}:{time_seed}:{group_id}"
            hash_value = int(hashlib.md5(hash_input.encode()).hexdigest(), 16)
            
            # 假设有4个Bot，使用哈希值对4取模来决定哪个Bot执行
            # 这里我们使用Bot ID的哈希来映射到0-3的范围
            bot_hash = int(hashlib.md5(bot_id.encode()).hexdigest(), 16) % 4
            selected = hash_value % 4
            
            should_execute = (bot_hash == selected)
            
            if self.config.enable_logging:
                logger.debug(format_log_message("随机选择", {
                    "command": command,
                    "bot_hash": bot_hash,
                    "selected": selected,
                    "execute": should_execute
                }))
            
            return should_execute
        
        elif strategy == BotSelectionStrategy.ROUND_ROBIN:
            # 基于时间窗口的轮询
            # 将时间划分为时间窗口大小的槽位
            time_slot = int(current_time / self.config.time_window)
            key = f"{group_id}:{command}:{time_slot}"
            
            # 获取或初始化计数器
            if key not in self._round_robin_counter:
                self._round_robin_counter[key] = 0
            else:
                self._round_robin_counter[key] += 1
            
            # 使用Bot ID的哈希作为Bot的序号（假设最多10个Bot）
            bot_index = int(hashlib.md5(bot_id.encode()).hexdigest(), 16) % 10
            counter = self._round_robin_counter[key]
            
            # 轮询：计数器对10取模，等于Bot序号的执行
            should_execute = (counter % 10 == bot_index)
            
            if self.config.enable_logging:
                logger.debug(format_log_message("轮询选择", {
                    "command": command,
                    "bot_index": bot_index,
                    "counter": counter,
                    "execute": should_execute
                }))
            
            return should_execute
        
        elif strategy == BotSelectionStrategy.PRIORITY:
            # 优先级策略：默认允许执行，依赖外部配置
            # 这里简化处理，总是允许第一个收到的Bot执行
            return True
        
        # 默认允许执行
        return True

    async def _handle_conflict(
        self,
        command_key: str,
        existing_record: CommandRecord,
        new_bot_id: str,
        current_time: float
    ) -> Optional[ConflictEvent]:
        """处理指令冲突
        
        Args:
            command_key: 指令键
            existing_record: 已存在的指令记录
            new_bot_id: 新请求的Bot ID
            current_time: 当前时间戳
            
        Returns:
            冲突事件对象
        """
        all_bots = [existing_record.bot_id]
        if new_bot_id not in all_bots:
            all_bots.append(new_bot_id)
        
        # 使用确定性选择
        selected_bot = await self._select_bot_deterministic(
            all_bots, existing_record.group_id, existing_record.command, current_time
        )
        
        blocked_bots = [bot for bot in all_bots if bot != selected_bot]
        
        conflict_event = ConflictEvent(
            command=existing_record.command,
            group_id=existing_record.group_id,
            timestamp=current_time,
            selected_bot_id=selected_bot,
            blocked_bots=blocked_bots,
            total_bots=len(all_bots)
        )
        
        self._record_conflict(conflict_event)
        
        if self.config.enable_logging:
            selected_bot_name = self._get_bot_name(selected_bot)
            blocked_names = [self._get_bot_name(bot) for bot in blocked_bots]
            
            logger.info(format_log_message("指令冲突", {
                "command": existing_record.command,
                "group": existing_record.group_id,
                "selected": f"{selected_bot_name}({selected_bot})",
                "blocked": ",".join([f"{n}({b})" for n, b in zip(blocked_names, blocked_bots)]),
                "total": len(all_bots)
            }))
        
        return conflict_event

    async def _select_bot_deterministic(
        self,
        bot_ids: List[str],
        group_id: str,
        command: str,
        current_time: float
    ) -> str:
        """确定性选择Bot
        
        使用基于消息哈希的确定性算法，确保所有Bot做出一致的选择。
        
        Args:
            bot_ids: 候选Bot ID列表
            group_id: 群组ID
            command: 指令名称
            current_time: 当前时间戳
            
        Returns:
            被选中的Bot ID
        """
        if not bot_ids:
            return ""
        
        if len(bot_ids) == 1:
            return bot_ids[0]
        
        strategy = BotSelectionStrategy(self.config.bot_selection_strategy)
        
        if strategy == BotSelectionStrategy.RANDOM:
            # 基于哈希的伪随机选择
            time_seed = int(current_time / self.config.time_window)
            hash_input = f"{group_id}:{command}:{time_seed}"
            hash_value = int(hashlib.md5(hash_input.encode()).hexdigest(), 16)
            
            # 对Bot列表排序后选择
            sorted_bots = sorted(bot_ids)
            selected_index = hash_value % len(sorted_bots)
            return sorted_bots[selected_index]
        
        elif strategy == BotSelectionStrategy.ROUND_ROBIN:
            # 基于时间窗口的轮询
            time_slot = int(current_time / self.config.time_window)
            key = f"{group_id}:{command}:{time_slot}"
            
            if key not in self._round_robin_counter:
                self._round_robin_counter[key] = 0
            
            counter = self._round_robin_counter[key]
            sorted_bots = sorted(bot_ids)
            selected_index = counter % len(sorted_bots)
            
            self._round_robin_counter[key] += 1
            return sorted_bots[selected_index]
        
        elif strategy == BotSelectionStrategy.PRIORITY:
            # 优先级选择
            bot_with_priority = [
                (bot_id, self._bot_registry.get(bot_id, BotInfo(bot_id, "", 0)).priority)
                for bot_id in bot_ids
            ]
            bot_with_priority.sort(key=lambda x: (-x[1], x[0]))  # 先按优先级降序，再按ID升序
            return bot_with_priority[0][0]
        
        return bot_ids[0]

    async def _add_to_cache(self, key: str, record: CommandRecord):
        """添加记录到缓存
        
        Args:
            key: 缓存键
            record: 指令记录
        """
        self._command_cache[key] = record
        self._command_cache.move_to_end(key)
        
        while len(self._command_cache) > self.config.max_cache_size:
            self._command_cache.popitem(last=False)

    def mark_command_completed(self, command: str, group_id: str, sender_id: str):
        """标记指令为已完成
        
        Args:
            command: 指令名称
            group_id: 群组ID
            sender_id: 发送者ID
        """
        command_key = generate_command_key(group_id, command, sender_id)
        if command_key in self._command_cache:
            self._command_cache[command_key].status = CommandStatus.COMPLETED

    def _record_conflict(self, event: ConflictEvent):
        """记录冲突事件
        
        Args:
            event: 冲突事件
        """
        self._conflict_history.append(event)
        
        while len(self._conflict_history) > self._max_history_size:
            self._conflict_history.pop(0)

    def get_conflict_history(self, limit: int = 50) -> List[ConflictEvent]:
        """获取冲突历史记录
        
        Args:
            limit: 返回的最大条数
            
        Returns:
            冲突事件列表
        """
        return self._conflict_history[-limit:]

    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息
        
        Returns:
            包含统计信息的字典
        """
        total_conflicts = len(self._conflict_history)
        
        if total_conflicts == 0:
            return {
                "total_conflicts": 0,
                "cache_size": len(self._command_cache),
                "registered_bots": len(self._bot_registry),
                "top_conflicted_commands": []
            }
        
        command_counts: Dict[str, int] = {}
        for event in self._conflict_history:
            command_counts[event.command] = command_counts.get(event.command, 0) + 1
        
        top_commands = sorted(
            command_counts.items(),
            key=lambda x: x[1],
            reverse=True
        )[:10]
        
        return {
            "total_conflicts": total_conflicts,
            "cache_size": len(self._command_cache),
            "registered_bots": len(self._bot_registry),
            "top_conflicted_commands": top_commands
        }

    async def _cleanup_loop(self):
        """定期清理循环"""
        while self._running:
            try:
                await asyncio.sleep(self.config.cleanup_interval)
                await self._cleanup_expired_records()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"清理任务出错: {e}", exc_info=True)

    async def _cleanup_expired_records(self):
        """清理过期的指令记录"""
        async with self._lock:
            current_time = get_current_timestamp()
            expired_keys = []
            
            for key, record in self._command_cache.items():
                if current_time - record.timestamp > COMMAND_CACHE_TTL:
                    expired_keys.append(key)
            
            for key in expired_keys:
                del self._command_cache[key]
            
            if expired_keys and self.config.enable_logging:
                logger.debug(f"清理了 {len(expired_keys)} 条过期记录")

    def _get_bot_name(self, bot_id: str) -> str:
        """获取Bot名称
        
        Args:
            bot_id: Bot ID
            
        Returns:
            Bot名称或Bot ID
        """
        bot_info = self._bot_registry.get(bot_id)
        return bot_info.bot_name if bot_info else bot_id