

from typing import AsyncGenerator, Any, Dict
import time

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api import AstrBotConfig

from .constants import MessageEmoji
from .models import PluginConfig
from .utils import handle_errors, format_log_message, extract_command


__version__ = "1.0.0"
__author__ = "yonglanws"


@register(
    "astrbot_plugin_block_duplicate_messages",
    "yonglanws",
    "多Bot指令冲突解决器 - 解决多Bot同时响应相同指令导致的刷屏问题",
    __version__,
    "https://github.com/yonglanws/astrbot_plugin_block_duplicate_messages"
)
class BlockDuplicateMessagesPlugin(Star):
    """多Bot指令冲突解决插件
    
    通过时间窗口检测和智能Bot选择策略，确保在多个Bot同时收到相同指令时，
    只有一个Bot执行该指令，避免刷屏问题。
    
    核心设计原则：
    - 最高优先级执行（priority=999）
    - 基于确定性哈希的Bot选择算法
    - 完善的错误处理和降级机制
    """

    def __init__(self, context: Context, config: AstrBotConfig = None):
        """初始化插件
        
        Args:
            context: AstrBot上下文对象
            config: 插件配置（从_conf_schema.json自动加载）
        """
        super().__init__(context)
        
        config_dict = dict(config) if config else {}
        self.config = PluginConfig.from_dict(config_dict)
        
        try:
            self.config.validate()
        except ValueError as e:
            logger.warning(f"[BDM] 配置验证失败，使用默认值: {e}")
            self.config = PluginConfig()
        
        # 核心数据结构：用于跟踪正在处理的指令
        self._processing_commands: Dict[str, float] = {}  # command_key -> timestamp
        
        self._initialized = False
        self._current_bot_id: str = ""
        self._current_bot_name: str = ""
        
        # 统计数据
        self._stats = {
            'total_blocked': 0,
            'total_allowed': 0,
            'conflict_events': []
        }

    async def initialize(self):
        """异步初始化方法"""
        try:
            self._initialize_bot_identity()
            
            self._initialized = True
            
            logger.info(
                f"[BDM] 插件初始化完成 | Bot={self._current_bot_name}({self._current_bot_id}) | "
                f"策略={self.config.bot_selection_strategy} | "
                f"窗口={self.config.time_window}s"
            )
        except Exception as e:
            logger.error(f"[BDM] 插件初始化失败: {e}", exc_info=True)
            raise

    def _initialize_bot_identity(self):
        """初始化Bot身份信息"""
        try:
            all_stars = self.context.get_all_stars()
            for star_meta in all_stars:
                if star_meta.instance is self:
                    self._current_bot_id = star_meta.name or id(self)
                    self._current_bot_name = getattr(star_meta, 'display_name', '') or self._current_bot_id
                    break
            
            if not self._current_bot_id:
                self._current_bot_id = f"bot_{id(self)}"
                self._current_bot_name = f"Bot_{id(self) % 10000}"
            
                
        except Exception as e:
            logger.warning(f"[BDM] 获取Bot身份失败，使用默认值: {e}")
            self._current_bot_id = f"bot_{id(self)}"
            self._current_bot_name = f"Bot_{id(self) % 10000}"

    def _get_bot_id_from_event(self, event: AstrMessageEvent) -> str:
        """从事件对象获取Bot ID
        
        Args:
            event: 消息事件对象
            
        Returns:
            Bot ID字符串
        """
        try:
            if hasattr(event.message_obj, 'self_id') and event.message_obj.self_id:
                return str(event.message_obj.self_id)
            if hasattr(event, 'platform_name'):
                return f"{event.platform_name}_{id(self)}"
        except Exception as e:
            logger.debug(f"[BDM] 获取Bot ID异常: {e}")
        return self._current_bot_id

    def _is_user_message(self, event: AstrMessageEvent) -> bool:
        """检查是否为用户发送的消息（非Bot自己发送的）
        
        Args:
            event: 消息事件对象
            
        Returns:
            True如果是用户消息，False如果是Bot消息
        """
        try:
            bot_id = self._get_bot_id_from_event(event)
            
            # 检查多种可能的sender属性
            sender_id = None
            
            # 方式1：通过message_obj.sender.user_id
            if hasattr(event.message_obj, 'sender') and hasattr(event.message_obj.sender, 'user_id'):
                sender_id = str(event.message_obj.sender.user_id)
            
            # 方式2：通过event.get_sender_id()
            if not sender_id and hasattr(event, 'get_sender_id'):
                sender_id = event.get_sender_id()
            
            # 如果能获取到sender_id且与bot_id不同，则是用户消息
            if sender_id and bot_id:
                return sender_id != bot_id
            
            return True  # 默认认为是用户消息
            
        except Exception as e:
            logger.debug(f"[BDM] 检查消息来源异常: {e}")
            return True  # 异常时默认处理

    def _generate_command_key(self, group_id: str, command: str, sender_id: str) -> str:
        """生成唯一的命令键
        
        使用简单字符串拼接替代MD5哈希，减少CPU计算。
        
        Args:
            group_id: 群组ID
            command: 指令标识
            sender_id: 发送者ID
            
        Returns:
            唯一的命令键
        """
        return f"{group_id}:{command}:{sender_id}"

    def _cleanup_expired_commands(self):
        """清理过期的命令记录"""
        current_time = time.time()
        expired_keys = [
            key for key, ts in self._processing_commands.items()
            if current_time - ts > self.config.time_window * 2
        ]
        
        for key in expired_keys:
            del self._processing_commands[key]

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=999)
    async def on_group_message(self, event: AstrMessageEvent):
        """监听群消息，检测并拦截重复指令
        
        这是核心拦截函数，具有最高优先级(999)，确保在其他插件之前执行。
        
        Args:
            event: 消息事件对象
        """
        if not self._initialized:
            return
        
        try:
            await self._handle_message(event)
        except Exception as e:
            logger.error(f"[BDM] 处理消息时出错: {e}", exc_info=True)

    def _extract_message_content(self, event: AstrMessageEvent) -> str:
        """提取消息内容，支持多种组件类型
        
        Args:
            event: 消息事件对象
            
        Returns:
            消息内容字符串
        """
        # 首先尝试获取普通文本
        message_str = (event.message_str or "").strip()
        if message_str:
            return message_str
        
        # 检查消息链中的JSON组件
        if hasattr(event.message_obj, 'message') and event.message_obj.message:
            for component in event.message_obj.message:
                # 检查是否为JSON组件
                comp_type = getattr(component, 'type', None)
                if comp_type and 'json' in str(comp_type).lower():
                    # 提取JSON内容
                    json_content = getattr(component, 'data', None) or getattr(component, 'content', None)
                    if json_content:
                        return f"[JSON]{json_content}"
        
        return ""

    async def _handle_message(self, event: AstrMessageEvent):
        """处理单条消息的核心逻辑
        
        Args:
            event: 消息事件对象
        """
        # 获取消息内容（支持文本、JSON等多种类型）
        message_str = self._extract_message_content(event)
        if not message_str:
            return
        
        # 提取指令/消息标识
        command = extract_command(message_str)
        if not command:
            return
        
        # 根据屏蔽模式决定是否处理
        is_command_msg = message_str.startswith('/') or message_str.startswith('[JSON]')
        if self.config.block_mode == "command" and not is_command_msg:
            return
        
        # 获取群组和发送者信息
        group_id = event.get_group_id() or ""
        sender_id = event.get_sender_id() or ""
        
        if not group_id or not sender_id:
            return
        
        # 过滤掉Bot自己发送的消息
        if not self._is_user_message(event):
            return
        
        # 获取当前Bot ID
        bot_id = self._get_bot_id_from_event(event)
        
        # 生成唯一命令键
        command_key = self._generate_command_key(group_id, command, sender_id)
        
        current_time = time.time()
        
        # 检查是否已有该命令在处理中
        if command_key in self._processing_commands:
            existing_time = self._processing_commands[command_key]
            time_diff = current_time - existing_time
            
            if time_diff <= self.config.time_window:
                # 在时间窗口内，阻止后续Bot
                self._stats['total_blocked'] += 1
                event.stop_event()
                return
            
            # 时间窗口已过，重新开始
            del self._processing_commands[command_key]
        
        # 第一个到达的Bot执行
        self._processing_commands[command_key] = current_time
        
        # 定期清理过期记录（每100次操作清理一次）
        if self._stats['total_allowed'] % 100 == 0:
            self._cleanup_expired_commands()
        
        self._stats['total_allowed'] += 1

    @filter.command("bdm_status")
    @handle_errors
    async def cmd_status(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        """查看插件状态和统计信息"""
        status_text = f"""{MessageEmoji.INFO} 多Bot指令冲突解决器状态 v{__version__}
━━━━━━━━━━━━━━━━━━
📊 运行状态: {'✅ 已启动' if self._initialized else '❌ 未初始化'}
🤖 当前Bot: {self._current_bot_name} ({self._current_bot_id})
⚙️ 选择策略: {self.config.bot_selection_strategy}
🛡️ 屏蔽模式: {self.config.block_mode}
⏱️ 时间窗口: {self.config.time_window}s
📦 缓存大小: {len(self._processing_commands)}/{self.config.max_cache_size}

📊 统计信息:
• 总放行数: {self._stats['total_allowed']}
• 总屏蔽数: {self._stats['total_blocked']}
• 冲突事件: {len(self._stats['conflict_events'])}"""
        
        yield event.plain_result(status_text)

    @filter.command("bdm_help")
    @handle_errors
    async def cmd_help(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        """显示帮助信息"""
        help_text = f"""{MessageEmoji.INFO} 多Bot指令冲突解决器 帮助 v{__version__}
━━━━━━━━━━━━━━━━━━
🔧 核心功能:
• 自动检测群内重复指令调用
• 智能选择一个Bot执行指令
• 屏蔽其他Bot的重复响应

📋 可用指令:
✳bdm_status - 查看运行状态和统计
✳bdm_config - 查看当前配置
✳bdm_help - 显示此帮助

⚙️ 配置说明:
• time_window: 冲突检测时间窗口(秒)
• bot_selection_strategy: Bot选择策略
  - random: 随机选择
  - round_robin: 轮询选择(默认)
  - priority: 优先级选择
• block_mode: 屏蔽模式
  - command: 仅屏蔽指令(/开头)
  - all: 屏蔽所有重复消息(默认)

💡 使用提示:
• 插件自动工作，无需手动干预
• 在WebUI中可调整配置参数
• 所有操作均有日志记录"""
        
        yield event.plain_result(help_text)

    @filter.command("bdm_config")
    @filter.permission_type(filter.PermissionType.ADMIN)
    @handle_errors
    async def cmd_config(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        """查看当前配置（仅管理员）"""
        config_text = f"""{MessageEmoji.INFO} 当前配置
━━━━━━━━━━━━━━━━━━
⏱️ 时间窗口: {self.config.time_window}s
🎯 选择策略: {self.config.bot_selection_strategy}
🛡️ 屏蔽模式: {self.config.block_mode}
📝 日志记录: {'开启' if self.config.enable_logging else '关闭'}
📊 日志级别: {self.config.log_level}
📦 最大缓存: {self.config.max_cache_size}
🧹 清理间隔: {self.config.cleanup_interval}s

💡 修改配置请前往 WebUI 的插件设置页面"""
        
        yield event.plain_result(config_text)

    async def terminate(self):
        """插件卸载时清理资源"""
        try:
            total_blocked = self._stats.get('total_blocked', 0)
            total_allowed = self._stats.get('total_allowed', 0)
            
            logger.info(
                f"[BDM] 插件已安全卸载 | "
                f"总放行={total_allowed} | 总屏蔽={total_blocked}"
            )
            
            # 清理资源
            self._processing_commands.clear()
            
        except Exception as e:
            logger.error(f"[BDM] 插件清理时出错: {e}", exc_info=True)
