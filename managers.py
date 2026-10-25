"""双层消息去重引擎

层1（同会话精确去重）：以 群ID+指令标识+发送者ID 为键，处理同一平台多账号
（如多个 OneBot v11 连接）同时收到同一消息的场景。此层 ID 语义在同平台内
一致，可精确匹配。

层2（跨平台指纹去重）：以归一化消息内容指纹为键，处理 OneBot v11 与
QQ 官方 API（qq_official / qq_official_webhook）等不同平台共处同一群聊时
的同消息多响应冲突。由于跨平台的群/用户标识完全不可对应（数字群号/QQ号
vs group_openid/member_openid，且官方 openid 每个应用各不相同），此层只能
依赖内容指纹+时间窗匹配，并通过群名锚定降低不同群同文本的误判率。

唤醒分级（woken）：
- 强记录：消息会唤醒机器人（唤醒前缀 / @本机 / 引用本机），机器人将真正
  响应。强记录可拦截任何后续匹配事件。
- 弱记录：消息不会唤醒机器人（如 @ 的是群里其他 bot），机器人自身不会
  响应。弱记录只拦截同样不会唤醒的后续事件——否则会出现"先到的无关事件
  压制了真正被 @ 的机器人的响应"导致全员沉默。
"""

import asyncio
from collections import OrderedDict
from typing import Dict, List, Optional, Tuple, Any

from astrbot.api import logger

from .constants import (
    GROUP_NAME_CACHE_TTL,
    GROUP_NAME_FAILURE_TTL,
    GROUP_NAME_RESOLVE_TIMEOUT,
    SWEEP_INTERVAL_OPS,
)
from .models import FingerprintRecord, MessageInfo, PluginConfig
from .utils import (
    content_fingerprint,
    format_log_message,
    generate_command_key,
    get_current_timestamp,
)


class DuplicateMessageFilter:
    """消息去重过滤器：同会话层 + 跨平台指纹层

    所有平台事件进入插件处理器时调用 check()，得到放行/拦截结论。
    事件由 AstrBot 并发处理，因此判定与记录都在 asyncio.Lock 内完成；
    唯一的例外是决策延迟等待（platform_priority + decision_delay_ms），
    等待期间不持锁，让更高优先级平台的事件有机会先登记。
    """

    def __init__(self, config: PluginConfig, context: Any = None):
        self.config = config
        self._context = context

        self._lock = asyncio.Lock()
        # 层1：command_key -> (timestamp, woken)
        self._layer1: Dict[str, Tuple[float, bool]] = {}
        # 层2：内容指纹 -> 记录列表（LRU）
        self._fingerprints: OrderedDict[str, List[FingerprintRecord]] = OrderedDict()
        # 群名解析缓存：group_key -> (过期时间, 群名或空串)
        self._group_name_cache: Dict[str, Tuple[float, str]] = {}
        self._ops_since_sweep = 0

        self.stats: Dict[str, Any] = {
            "total_allowed": 0,
            "blocked_same_conversation": 0,
            "blocked_cross_platform": 0,
            "cross_events": [],  # 最近跨平台拦截记录
        }

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------

    async def check(self, info: MessageInfo) -> Tuple[bool, str]:
        """判定一条群消息是否放行

        Args:
            info: 从事件提取的判定信息

        Returns:
            (是否放行, 拦截原因描述)
        """
        self._sweep_if_needed(get_current_timestamp())

        fingerprint = ""
        if self._cross_enabled_for(info):
            fingerprint = content_fingerprint(info.message_str)
        key1 = generate_command_key(info.group_id, info.command, info.sender_id)

        async with self._lock:
            now = get_current_timestamp()

            # ---- 层1：同会话精确去重（同平台多账号） ----
            existing = self._layer1.get(key1)
            if existing is not None:
                ts, woken = existing
                if now - ts <= self.config.time_window:
                    if woken or not info.woken:
                        self.stats["blocked_same_conversation"] += 1
                        return False, "同会话重复消息"
                    # 已有弱记录而当前事件是唤醒事件：不拦截，
                    # 落记录时会升级为强记录（真正要响应的机器人到达了）
                else:
                    del self._layer1[key1]

            # ---- 层2：跨平台指纹去重 ----
            if fingerprint:
                matched = await self._find_cross_match(info, fingerprint, now)
                if matched is not None:
                    self._record_cross_block(info, matched)
                    return False, self._describe_match(matched)

            # ---- 决策延迟：给更高优先级平台一个先登记的机会 ----
            # 注意：不能在锁内 sleep，否则对方事件连 check 都进不来。
            if not (fingerprint and await self._should_delay(info.platform_name)):
                self._record_allow(info, key1, fingerprint)
                return True, ""

        # 延迟等待期间不持锁；醒来后重新扫描再决策
        await asyncio.sleep(self.config.decision_delay_ms / 1000.0)
        async with self._lock:
            matched = await self._find_cross_match(
                info, fingerprint, get_current_timestamp()
            )
            if matched is not None:
                self._record_cross_block(info, matched)
                return False, self._describe_match(matched)
            self._record_allow(info, key1, fingerprint)
            return True, ""

    # ------------------------------------------------------------------
    # 层1 辅助
    # ------------------------------------------------------------------

    def _record_allow(self, info: MessageInfo, key1: str, fingerprint: str) -> None:
        now = get_current_timestamp()
        self._layer1[key1] = (now, info.woken)
        if fingerprint:
            records = self._fingerprints.setdefault(fingerprint, [])
            records.append(
                FingerprintRecord(
                    platform_name=info.platform_name,
                    platform_id=info.platform_id,
                    self_id=info.self_id,
                    group_id=info.group_id,
                    group_name=info.group_name,
                    sender_id=info.sender_id,
                    fingerprint=fingerprint,
                    woken=info.woken,
                    timestamp=now,
                )
            )
            self._fingerprints.move_to_end(fingerprint)
            self._cap_fingerprints()
        self.stats["total_allowed"] += 1

    # ------------------------------------------------------------------
    # 层2 辅助
    # ------------------------------------------------------------------

    def _cross_enabled_for(self, info: MessageInfo) -> bool:
        """判断该事件是否参与跨平台去重"""
        if not self.config.cross_platform_dedup:
            return False
        if info.platform_name not in self._qq_family_platforms():
            return False
        # qq_official 系平台的群消息 group_id 是 group_openid（含字母），
        # 频道消息的 group_id 是纯数字 channel_id；OneBot 无法出现在频道，
        # 与频道消息做内容匹配必然是误判，直接跳过。
        if info.platform_name.startswith("qq_official") and info.group_id.isdigit():
            return False
        return True

    def _qq_family_platforms(self) -> List[str]:
        return [
            p.strip()
            for p in self.config.cross_platform_platforms.split(",")
            if p.strip()
        ]

    def _priority_list(self) -> List[str]:
        return [
            p.strip()
            for p in self.config.platform_priority.split(",")
            if p.strip()
        ]

    async def _find_cross_match(
        self, info: MessageInfo, fingerprint: str, now: float
    ) -> Optional[FingerprintRecord]:
        """在指纹记录中查找与当前事件冲突的记录

        匹配规则：
        1. 唤醒分级：强记录可拦截任何事件；弱记录只拦截非唤醒事件。
        2. 跨平台（platform_name 不同）：群名锚定——若双方群名都已知且
           不同，视为不同群，不构成冲突；否则视为同消息冲突。
        3. 同平台不同会话：仅当双方群名都已知且相同（几乎必为同群）时
           才视为冲突，其余放行交给层1处理。
        4. 同平台同会话：视为冲突（覆盖层1窗口外但跨平台窗口内的场景）。
        """
        records = self._fingerprints.get(fingerprint)
        if not records:
            return None

        window = self.config.cross_platform_time_window
        for record in records:
            if now - record.timestamp > window:
                continue
            if record.woken is False and info.woken:
                # 弱记录不压制唤醒事件
                continue
            if record.platform_name == info.platform_name:
                same_conversation = (
                    record.group_id == info.group_id
                    and record.sender_id == info.sender_id
                )
                if same_conversation:
                    return record
                if self.config.use_group_name_anchor:
                    name_a = record.group_name or await self._resolve_group_name(record)
                    if name_a and info.group_name and name_a == info.group_name:
                        return record
                continue
            # 不同平台
            if self.config.use_group_name_anchor and info.group_name:
                name_a = record.group_name or await self._resolve_group_name(record)
                if name_a and name_a != info.group_name:
                    # 群名不同 → 不同的群，非重复
                    continue
            return record
        return None

    async def _resolve_group_name(self, record: FingerprintRecord) -> str:
        """解析记录对应群名（仅支持 OneBot 系平台，带缓存）

        QQ 官方侧的群名在事件到达时已由适配器尽力填充；OneBot 侧事件通常
        不带群名，需要时通过 get_group_info 查询。
        """
        if not record.platform_name.startswith("aiocqhttp"):
            return record.group_name
        cache_key = f"{record.platform_id}:{record.group_id}"
        now = get_current_timestamp()
        cached = self._group_name_cache.get(cache_key)
        if cached and cached[0] > now:
            return cached[1]

        name = ""
        try:
            inst = None
            if self._context is not None and hasattr(self._context, "get_platform_inst"):
                inst = self._context.get_platform_inst(record.platform_id)
            bot = getattr(inst, "bot", None)
            if bot is not None and hasattr(bot, "call_action"):
                result = await asyncio.wait_for(
                    bot.call_action("get_group_info", group_id=int(record.group_id)),
                    timeout=GROUP_NAME_RESOLVE_TIMEOUT,
                )
                if isinstance(result, dict):
                    name = str(result.get("group_name") or "")
        except Exception as e:
            logger.debug(f"[BDM] 查询群名失败 group={record.group_id}: {e}")

        ttl = GROUP_NAME_CACHE_TTL if name else GROUP_NAME_FAILURE_TTL
        self._group_name_cache[cache_key] = (now + ttl, name)
        return name

    async def _should_delay(self, platform_name: str) -> bool:
        """是否需要为更高优先级平台等待决策

        条件：配置了 platform_priority 与 decision_delay_ms，且当前已连接
        的 QQ 系平台中存在比本平台优先级更高的平台。
        """
        if self.config.decision_delay_ms <= 0:
            return False
        priorities = self._priority_list()
        if not priorities or platform_name not in priorities:
            return False
        try:
            instances = self._context.platform_manager.platform_insts
            connected = {p.meta().name for p in instances}
        except Exception:
            return False
        for candidate in priorities:
            if candidate == platform_name:
                return False
            if candidate in connected:
                return True
        return False

    def _describe_match(self, record: FingerprintRecord) -> str:
        if record.platform_name == "":
            return "重复消息"
        return f"跨平台重复(vs {record.platform_name})"

    def _record_cross_block(self, info: MessageInfo, matched: FingerprintRecord) -> None:
        self.stats["blocked_cross_platform"] += 1
        event_desc = {
            "platform": info.platform_name,
            "vs_platform": matched.platform_name,
            "group_id": info.group_id,
            "sender_id": info.sender_id,
            "message": info.message_str[:40],
            "timestamp": get_current_timestamp(),
        }
        self.stats["cross_events"].append(event_desc)
        if len(self.stats["cross_events"]) > 50:
            self.stats["cross_events"].pop(0)
        if self.config.enable_logging:
            logger.info(
                format_log_message("跨平台拦截", {
                    "platform": info.platform_name,
                    "vs": matched.platform_name,
                    "group": info.group_id,
                    "sender": info.sender_id,
                    "msg": info.message_str[:30],
                })
            )

    # ------------------------------------------------------------------
    # 缓存维护
    # ------------------------------------------------------------------

    def _cap_fingerprints(self) -> None:
        """限制指纹缓存容量并清理过期记录"""
        while len(self._fingerprints) > self.config.max_cache_size:
            self._fingerprints.popitem(last=False)

    def _sweep_if_needed(self, now: float) -> None:
        """周期性清扫过期缓存（每 SWEEP_INTERVAL_OPS 次操作一次）"""
        self._ops_since_sweep += 1
        if self._ops_since_sweep < SWEEP_INTERVAL_OPS:
            return
        self._ops_since_sweep = 0

        layer1_ttl = max(self.config.time_window, 10.0) * 2
        expired = [
            key for key, (ts, _) in self._layer1.items() if now - ts > layer1_ttl
        ]
        for key in expired:
            del self._layer1[key]

        window = self.config.cross_platform_time_window
        for fingerprint in list(self._fingerprints.keys()):
            records = [
                r for r in self._fingerprints[fingerprint] if now - r.timestamp <= window
            ]
            if records:
                self._fingerprints[fingerprint] = records
            else:
                del self._fingerprints[fingerprint]

        for cache_key in list(self._group_name_cache.keys()):
            if self._group_name_cache[cache_key][0] <= now:
                del self._group_name_cache[cache_key]

    def get_status_summary(self) -> Dict[str, Any]:
        """获取运行状态摘要（供状态指令使用）"""
        return {
            "total_allowed": self.stats["total_allowed"],
            "blocked_same_conversation": self.stats["blocked_same_conversation"],
            "blocked_cross_platform": self.stats["blocked_cross_platform"],
            "layer1_size": len(self._layer1),
            "fingerprint_size": len(self._fingerprints),
            "cross_platform_enabled": self.config.cross_platform_dedup,
            "qq_family": self._qq_family_platforms(),
        }

    def clear(self) -> None:
        """清空全部缓存"""
        self._layer1.clear()
        self._fingerprints.clear()
        self._group_name_cache.clear()
