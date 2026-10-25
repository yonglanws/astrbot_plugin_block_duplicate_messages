# 多Bot指令冲突解决器 (astrbot_plugin_block_duplicate_messages)

[![AstrBot](https://img.shields.io/badge/AstrBot-v3.4+-blue.svg)](https://github.com/AstrBotDevs/AstrBot)
[![Version](https://img.shields.io/badge/version-2.1.0-green.svg)](https://github.com/yaohewoma/astrbot_plugin_block_duplicate_messages)
[![License](https://img.shields.io/badge/license-GPLv3-blue.svg)](LICENSE)

> 解决多Bot同时响应相同指令导致的刷屏问题。v2.1 起同时支持 **同平台多账号**（多个 OneBot v11 连接）与 **跨平台共群**（OneBot v11 + QQ 官方 API）两种冲突场景的去重。

## 🎯 功能特性

- **🔍 同会话冲突检测**：同一平台多账号（如多个 OneBot v11 连接）收到同一消息时，时间窗口内只放行一个
- **🌐 跨平台冲突检测（v2.1 新增）**：OneBot v11 机器人与 QQ 官方 API 机器人（`qq_official` / `qq_official_webhook`）共处同一群聊时，识别同一条消息产生的两个平台事件，只放行一个
- **⚓ 群名锚定防误判**：跨平台匹配时若双方群名都已知且不同，判定为不同群不拦截；OneBot 侧群名缺失时自动调用 `get_group_info` 查询（带缓存）
- **🔔 唤醒分级防误杀**：`@` 其他 bot 的消息（本机不会响应）只作为弱记录，不会压制真正被 `@` 的机器人的响应，避免"全员沉默"
- **🎯 平台优先级**：可配置 `platform_priority` + `decision_delay_ms`，让指定平台优先响应，其余平台短暂等待后让位
- **⚙️ 灵活配置**：窗口、范围、模式等均可通过 WebUI 调整
- **📊 统计与日志**：`bdm_status` 查看同会话/跨平台拦截统计

## 📦 安装

```bash
cd AstrBot/data/plugins
git clone https://github.com/yaohewoma/astrbot_plugin_block_duplicate_messages.git
```

重启 AstrBot 或在 WebUI 插件管理页重载本插件。

## 🚀 使用方法

插件自动工作，无需手动干预。管理员可通过指令查看状态：

| 指令 | 说明 | 权限 |
|------|------|------|
| `/bdm_status` | 查看运行状态和统计信息 | 所有人 |
| `/bdm_config` | 查看当前配置 | 管理员 |
| `/bdm_help` | 显示帮助信息 | 所有人 |

## ⚙️ 配置参数

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `time_window` | 浮点数 | 2.0 | 同会话（同平台多账号）冲突检测窗口（秒） |
| `block_mode` | 字符串 | all | command=仅指令，all=所有重复消息 |
| `cross_platform_dedup` | 布尔值 | true | 跨平台去重开关 |
| `cross_platform_time_window` | 浮点数 | 5.0 | 跨平台检测窗口（秒）。官方 API 推送常比 OneBot 慢，建议 3-10 |
| `cross_platform_platforms` | 字符串 | aiocqhttp,qq_official,qq_official_webhook | 参与跨平台去重的平台白名单 |
| `platform_priority` | 字符串 | （空） | 平台响应优先级，逗号分隔，如 `aiocqhttp,qq_official`；留空为先到先得 |
| `decision_delay_ms` | 整数 | 0 | 非最高优先级平台的决策延迟（毫秒），建议 300-800；0 关闭 |
| `use_group_name_anchor` | 布尔值 | true | 群名锚定，双方群名都已知且不同时不拦截 |
| `enable_logging` | 布尔值 | true | 详细日志 |
| `max_cache_size` | 整数 | 1000 | 最大缓存条目数 |

## 🎮 工作原理

### 场景一：同平台多账号（多个 OneBot v11 连接）

两个 OneBot 协议端（NapCat/Lagrange 等）登录不同 bot 账号、共处同一群。同一条群消息被两个适配器各产生一个事件，但两者的 `group_id`/`sender_id` 都是真实的数字群号和 QQ 号，完全一致。

**层1（同会话精确去重）** 以 `群ID + 指令标识 + 发送者ID` 为键，窗口内（默认 2s）第二个到达的事件被 `stop_event()` 拦截。

### 场景二：跨平台共群（OneBot v11 + QQ 官方 API）—— v2.1 解决的问题

用户在群里发送 `/help`，群里的 OneBot bot 与官方 API bot 都被唤醒并响应。此前的插件无法处理，因为**两个平台对同一群、同一用户的标识完全无法对应**：

| 字段 | OneBot v11 | QQ 官方 API |
|------|-----------|-------------|
| 群标识 | 数字群号（如 `123456`） | `group_openid`（随机串，且每个官方 App 各不相同） |
| 用户标识 | QQ 号 | `member_openid`（每个官方 App 各不相同） |

因此 `群ID+发送者+指令` 的键跨平台永远不会碰撞，两边都放行、双双响应。

**层2（跨平台内容指纹去重）** 改用归一化消息内容指纹 + 更长的时间窗（默认 5s）匹配：

```
用户在群里发 "/help"
        ↓
┌─ onebot 事件 (群123456, QQ10001)      ──→ 指纹 a1b2 → 放行，记录
└─ qq_official 事件 (group_openid, member_openid) ──→ 指纹 a1b2
        ↓ 内容指纹相同、窗口内、群名锚定通过
   拦截 qq_official 事件 (stop_event)
```

配套三个防误判机制：

1. **平台白名单**：只在 `cross_platform_platforms` 列表内的平台之间做内容匹配，Telegram/Discord 等互不相干的平台不受影响。
2. **频道守卫**：`qq_official` 的频道消息 `group_id` 是纯数字 `channel_id`，OneBot 不可能出现在频道，直接跳过匹配。
3. **群名锚定**：OneBot 侧群名可从事件或 `get_group_info` 获取，官方侧群名由适配器通过官方 API 填充。双方群名都已知且**不同**时判定为不同群、不拦截；任一侧缺失时回退为纯内容匹配。

### 唤醒分级（防"全员沉默"）

用户发 `@官方bot /help` 时，OneBot bot 并未被唤醒（不会响应），官方 bot 才是响应者。若把 OneBot 那条"不会响应"的事件也当作强去重记录，官方 bot 会被误拦，导致没人回复。

本插件读取 AstrBot 管道已算好的 `is_at_or_wake_command`：

- **强记录**（会唤醒本机，本机会响应）：可拦截任何后续匹配事件
- **弱记录**（不会唤醒本机，本机不会响应）：只拦截同样不唤醒的事件

### 平台优先级（可选）

默认先到先得（通常是 OneBot 更快）。若希望固定由某个平台响应：

1. `platform_priority` 填如 `aiocqhttp,qq_official`（OneBot 优先）
2. `decision_delay_ms` 设 300-800：非最高优先级平台收到消息后先等待，期间高优先级平台登记了同一消息则让位

注意：多个 bot 必须连接在**同一个 AstrBot 实例**中（本插件在进程内去重）。跨 AstrBot 实例的冲突无法由本插件处理。

## 📝 更新日志

### v2.1.0 (2026-10-25)
- ✨ 新增跨平台去重：解决 OneBot v11 与 QQ 官方 API（qq_official）共群时同一消息双响应的问题
- ✨ 新增内容指纹归一化（剥离官方 `@` 标记、压缩空白）
- ✨ 新增群名锚定与频道守卫，降低跨平台误判
- ✨ 新增唤醒强弱分级，修复 `@` 指定 bot 时可能全员沉默的问题
- ✨ 新增 `platform_priority` + `decision_delay_ms` 平台优先级机制
- ♻️ 重构去重引擎为双层结构（同会话层 / 跨平台指纹层）
- 🔐 指纹哈希由 MD5 换为 SHA-256

### v1.0.0 (2026-04-14)
- ✨ 初始版本发布

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 📄 许可证

本项目采用 [GPLv3](LICENSE) 许可证。

## 🔗 相关链接

- [AstrBot 官方仓库](https://github.com/AstrBotDevs/AstrBot)
- [AstrBot 插件开发文档](https://docs.astrbot.app/dev/star/plugin-new.html)
- [AstrBot 插件市场](https://github.com/AstrBotDevs/AstrBot_Plugins)

---

**Made with ❤️ by 慵懒午睡**
