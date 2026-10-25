

from typing import AsyncGenerator, Any

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api import AstrBotConfig

from .constants import MessageEmoji
from .managers import DuplicateMessageFilter
from .models import MessageInfo, PluginConfig
from .utils import handle_errors, extract_command


__version__ = "2.1.0"
__author__ = "yonglanws"


@register(
    "astrbot_plugin_block_duplicate_messages",
    "yonglanws",
    "多Bot指令冲突解决器 - 解决多Bot（含 OneBot v11 与 QQ 官方 API 跨平台）同时响应相同指令导致的刷屏问题",
    __version__,
    "https://github.com/yonglanws/astrbot_plugin_block_duplicate_messages"
)
class BlockDuplicateMessagesPlugin(Star):
    """多Bot指令冲突解决插件

    通过时间窗口检测与双层去重，确保多个机器人（无论同平台多账号还是
    OneBot v11 + QQ 官方 API 跨平台共群）收到相同消息时只有一个响应。

    核心设计：
    - 最高优先级监听（priority=999），在其他插件之前判定
    - 层1：同会话精确去重（群ID+指令+发送者，同平台多账号场景）
    - 层2：跨平台内容指纹去重（OneBot v11 与 qq_official 等共群场景，
      配合群名锚定与唤醒强弱分级防止误杀）
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

        self._filter = DuplicateMessageFilter(self.config, context)
        self._initialized = False

    async def initialize(self):
        """异步初始化方法"""
        try:
            connected = []
            try:
                for inst in self.context.platform_manager.platform_insts:
                    meta = inst.meta()
                    connected.append(f"{meta.name}({meta.id})")
            except Exception:
                pass

            logger.info(
                f"[BDM] 插件初始化完成 | 已连接平台: {', '.join(connected) or '无'} | "
                f"同会话窗口={self.config.time_window}s | "
                f"跨平台去重={'开' if self.config.cross_platform_dedup else '关'} "
                f"窗口={self.config.cross_platform_time_window}s | "
                f"跨平台范围={self.config.cross_platform_platforms}"
            )

            self._initialized = True

        except Exception as e:
            logger.error(f"[BDM] 插件初始化失败: {e}", exc_info=True)
            raise

    def _get_woken(self, event: AstrMessageEvent) -> bool:
        """判断消息是否唤醒了本机器人（会真正响应）

        event.is_at_or_wake_command 由管道的唤醒检查阶段在插件处理前设置：
        唤醒前缀、@本机、引用本机、私聊等情况为 True。
        非唤醒消息（如 @ 的是群里其他 bot）本机不会响应，只作为弱记录参与去重。
        """
        try:
            return bool(getattr(event, "is_at_or_wake_command", True))
        except Exception:
            return True

    def _get_group_name(self, event: AstrMessageEvent) -> str:
        """从事件中提取群名（可能为空）"""
        try:
            group = getattr(event.message_obj, "group", None)
            name = getattr(group, "group_name", None)
            return str(name) if name else ""
        except Exception:
            return ""

    def _is_user_message(self, event: AstrMessageEvent) -> bool:
        """过滤机器人自己发送的消息"""
        try:
            self_id = event.get_self_id()
            sender_id = event.get_sender_id()
            if self_id and sender_id:
                return str(sender_id) != str(self_id)
        except Exception:
            pass
        return True

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=999)
    async def on_group_message(self, event: AstrMessageEvent):
        """监听群消息，检测并拦截重复指令

        最高优先级(999)，确保在其他插件之前判定。
        """
        if not self._initialized:
            return

        try:
            await self._handle_message(event)
        except Exception as e:
            logger.error(f"[BDM] 处理消息时出错: {e}", exc_info=True)

    def _extract_message_content(self, event: AstrMessageEvent) -> str:
        """提取消息内容，支持多种组件类型"""
        # 首先尝试获取普通文本
        message_str = (event.message_str or "").strip()
        if message_str:
            return message_str

        # 检查消息链中的JSON组件
        if hasattr(event.message_obj, 'message') and event.message_obj.message:
            for component in event.message_obj.message:
                comp_type = getattr(component, 'type', None)
                if comp_type and 'json' in str(comp_type).lower():
                    json_content = getattr(component, 'data', None) or getattr(component, 'content', None)
                    if json_content:
                        return f"[JSON]{json_content}"

        return ""

    async def _handle_message(self, event: AstrMessageEvent):
        """处理单条消息的核心逻辑"""
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

        group_id = event.get_group_id() or ""
        sender_id = event.get_sender_id() or ""

        if not group_id or not sender_id:
            return

        # 过滤掉Bot自己发送的消息
        if not self._is_user_message(event):
            return

        info = MessageInfo(
            platform_name=event.get_platform_name() or "",
            platform_id=event.get_platform_id() or "",
            self_id=event.get_self_id() or "",
            group_id=group_id,
            group_name=self._get_group_name(event),
            sender_id=sender_id,
            message_str=message_str,
            command=command,
            woken=self._get_woken(event),
        )

        allowed, reason = await self._filter.check(info)

        if not allowed:
            if self.config.enable_logging:
                logger.info(
                    f"[BDM] 拦截重复消息 | 原因={reason} | 平台={info.platform_name} | "
                    f"群={info.group_id} | 发送者={info.sender_id} | 内容={message_str[:30]}"
                )
            event.stop_event()
            return

    @filter.command("bdm_status")
    @handle_errors
    async def cmd_status(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        """查看插件状态和统计信息"""
        s = self._filter.get_status_summary()
        cross_events = self._filter.stats["cross_events"]
        recent = ""
        if cross_events:
            last = cross_events[-1]
            recent = (
                f"\n🕒 最近跨平台拦截: {last['platform']} vs {last['vs_platform']} | "
                f"{last['message']}"
            )

        status_text = f"""{MessageEmoji.INFO} 多Bot指令冲突解决器状态 v{__version__}
━━━━━━━━━━━━━━━━━━
📊 运行状态: {'✅ 已启动' if self._initialized else '❌ 未初始化'}
🛡️ 屏蔽模式: {self.config.block_mode}
⏱️ 同会话窗口: {self.config.time_window}s

🌐 跨平台去重: {'✅ 开启' if s['cross_platform_enabled'] else '❌ 关闭'}
• 适用平台: {', '.join(s['qq_family'])}
• 时间窗口: {self.config.cross_platform_time_window}s
• 群名锚定: {'开' if self.config.use_group_name_anchor else '关'}
• 平台优先级: {self.config.platform_priority or '未设置(先到先得)'}
• 决策延迟: {self.config.decision_delay_ms}ms

📊 统计信息:
• 总放行数: {s['total_allowed']}
• 同会话拦截: {s['blocked_same_conversation']}
• 跨平台拦截: {s['blocked_cross_platform']}
• 层1缓存: {s['layer1_size']} | 指纹缓存: {s['fingerprint_size']}{recent}"""

        yield event.plain_result(status_text)

    @filter.command("bdm_help")
    @handle_errors
    async def cmd_help(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        """显示帮助信息"""
        help_text = f"""{MessageEmoji.INFO} 多Bot指令冲突解决器 帮助 v{__version__}
━━━━━━━━━━━━━━━━━━
🔧 核心功能:
• 同会话去重: 同平台多账号(如多个OneBot连接)响应同一消息时只放行一个
• 跨平台去重: OneBot v11 与 QQ官方API(qq_official)共处同一群聊时,
  识别同一条消息的两个平台事件, 只放行一个
• 唤醒分级: @ 其他bot的消息不会误杀真正被@机器人的响应

📋 可用指令:
✳bdm_status - 查看运行状态和统计
✳bdm_config - 查看当前配置
✳bdm_help - 显示此帮助

⚙️ 重要配置(WebUI插件设置页):
• time_window: 同会话冲突检测窗口(秒)
• cross_platform_dedup: 跨平台去重开关(默认开)
• cross_platform_time_window: 跨平台检测窗口(秒,默认5)
• cross_platform_platforms: 参与跨平台去重的平台
• platform_priority: 平台优先级, 如 aiocqhttp,qq_official
• decision_delay_ms: 非优先平台决策延迟(毫秒)
• use_group_name_anchor: 群名锚定防误判(默认开)
• block_mode: command=仅指令 / all=所有消息

💡 使用提示:
• 插件自动工作，无需手动干预
• qqofficial 事件推送常有延迟, 若仍有双响应可调大跨平台窗口
• 想固定由某平台响应时, 配置 platform_priority 并给
  decision_delay_ms 设 300-800ms"""

        yield event.plain_result(help_text)

    @filter.command("bdm_config")
    @filter.permission_type(filter.PermissionType.ADMIN)
    @handle_errors
    async def cmd_config(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        """查看当前配置（仅管理员）"""
        config_text = f"""{MessageEmoji.INFO} 当前配置
━━━━━━━━━━━━━━━━━━
⏱️ 同会话时间窗口: {self.config.time_window}s
🛡️ 屏蔽模式: {self.config.block_mode}
🌐 跨平台去重: {'开启' if self.config.cross_platform_dedup else '关闭'}
⏱️ 跨平台时间窗口: {self.config.cross_platform_time_window}s
📋 跨平台范围: {self.config.cross_platform_platforms}
🎯 平台优先级: {self.config.platform_priority or '未设置(先到先得)'}
⏳ 决策延迟: {self.config.decision_delay_ms}ms
⚓ 群名锚定: {'开启' if self.config.use_group_name_anchor else '关闭'}
📝 日志记录: {'开启' if self.config.enable_logging else '关闭'}
📦 最大缓存: {self.config.max_cache_size}

💡 修改配置请前往 WebUI 的插件设置页面"""

        yield event.plain_result(config_text)

    async def terminate(self):
        """插件卸载时清理资源"""
        try:
            s = self._filter.get_status_summary()
            logger.info(
                f"[BDM] 插件已安全卸载 | "
                f"总放行={s['total_allowed']} | "
                f"同会话拦截={s['blocked_same_conversation']} | "
                f"跨平台拦截={s['blocked_cross_platform']}"
            )
            self._filter.clear()
        except Exception as e:
            logger.error(f"[BDM] 插件清理时出错: {e}", exc_info=True)
