# 多Bot指令冲突解决器 (astrbot_plugin_block_duplicate_messages)

[![AstrBot](https://img.shields.io/badge/AstrBot-v3.4+-blue.svg)](https://github.com/AstrBotDevs/AstrBot)
[![Version](https://img.shields.io/badge/version-1.0.0-green.svg)](https://github.com/yaohewoma/astrbot_plugin_block_duplicate_messages)
[![License](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)

> 解决多Bot同时响应相同指令导致的刷屏问题，实现智能指令分发与屏蔽

## 🎯 功能特性

- **🔍 智能冲突检测**：实时监控群组内的指令调用，在可配置的时间窗口内识别重复指令
- **🤖 智能Bot选择**：支持随机、轮询、优先级三种选择策略，确保公平且有效地选择一个Bot执行
- **🚫 自动屏蔽**：自动屏蔽其他Bot的重复响应，避免刷屏
- **⚙️ 灵活配置**：提供丰富的可配置参数，适应不同使用场景
- **📊 统计与日志**：详细的冲突记录和统计分析，便于优化
- **🔒 高可靠性**：完善的错误处理和异常降级机制

## 📦 安装

### 方式一：直接下载
1. 下载本插件源码
2. 解压到 AstrBot 的 `data/plugins/` 目录下
3. 确保文件夹名称为 `astrbot_plugin_block_duplicate_messages`

### 方式二：Git Clone
```bash
cd AstrBot/data/plugins
git clone https://github.com/yaohewoma/astrbot_plugin_block_duplicate_messages.git
```

## 🚀 使用方法

### 1. 启用插件
在 AstrBot WebUI 的插件管理页面找到本插件，点击启用。

### 2. 配置参数
在 WebUI 的插件设置中调整以下参数：

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `time_window` | 浮点数 | 2.0 | 冲突检测时间窗口（秒），建议 1-3 秒 |
| `bot_selection_strategy` | 字符串 | round_robin | Bot选择策略：random/round_robin/priority |
| `enable_logging` | 布尔值 | true | 是否启用详细日志记录 |
| `log_level` | 字符串 | INFO | 日志级别：DEBUG/INFO/WARNING/ERROR |
| `max_cache_size` | 整数 | 1000 | 最大缓存条目数，防止内存溢出 |
| `cleanup_interval` | 整数 | 300 | 缓存清理间隔（秒） |

### 3. 使用指令

| 指令 | 说明 | 权限 |
|------|------|------|
| `✳bdm_status` | 查看插件运行状态和统计信息 | 所有人 |
| `✳bdm_help` | 显示帮助信息 | 所有人 |
| `✳bdm_config` | 查看当前配置 | 管理员 |
| `✳bdm_history [N]` | 查看最近N条冲突记录（默认10条） | 所有人 |

## 🎮 工作原理

### 冲突检测流程

```
用户发送指令 /help
        ↓
插件检测到群消息中的指令
        ↓
检查时间窗口内是否有相同指令
        ↓
├─ 无冲突 → 允许当前Bot执行
└─ 有冲突 → 执行Bot选择策略
        ↓
  选择一个Bot执行，屏蔽其他Bot
```

### Bot选择策略

1. **随机选择 (random)**
   - 在冲突的Bot中随机选择一个执行
   - 优点：完全公平
   - 缺点：不可预测

2. **轮询选择 (round_robin)** - 默认
   - 按顺序轮流选择每个Bot
   - 优点：公平且有序，负载均衡
   - 缺点：无优先级区分

3. **优先级选择 (priority)**
   - 按优先级排序后选择
   - 优点：可配置高性能Bot优先
   - 缺点：需要预先配置优先级

## 📋 使用示例

### 场景：群内4个Bot同时响应 /help 指令

**未安装插件时：**
```
用户: /help
Bot1: 帮助信息...
Bot2: 帮助信息...
Bot3: 帮助信息...
Bot4: 帮助信息...
```
（刷屏，用户体验差）

**安装插件后：**
```
用户: /help
Bot2: 帮助信息...
```
（只有一个Bot响应，干净整洁）

### 查看状态
```
用户: ✳bdm_status
Bot: ℹ️ 多Bot指令冲突解决器状态
━━━━━━━━━━━━━━━━━━
📊 运行状态: ✅ 已启动
🤖 当前Bot: Bot_1234
⚙️ 选择策略: round_robin
⏱️ 时间窗口: 2.0s
💥 冲突总数: 156
```

### 查看冲突历史
```
用户: ✳bdm_history 5
Bot: ℹ️ 最近 5 条冲突记录
━━━━━━━━━━━━━━━━━━
【1】14:32:15
  指令: /help
  群组: 123456789
  执行Bot: ⭐Bot_2
  屏蔽Bot: 🚫Bot_1,Bot_3,Bot_4
```

## ⚙️ 高级配置

### 调整时间窗口
如果网络延迟较大，可以适当增加时间窗口：
```json
{
  "time_window": 3.5
}
```

### 启用调试日志
排查问题时可以启用DEBUG级别日志：
```json
{
  "log_level": "DEBUG",
  "enable_logging": true
}
```

## 🔧 技术架构

### 项目结构
```
astrbot_plugin_block_duplicate_messages/
├── main.py              # 主入口文件
├── managers.py          # 冲突检测管理器（核心算法）
├── models.py            # 数据模型
├── constants.py         # 常量定义
├── utils.py             # 工具函数
├── _conf_schema.json    # 配置模式
└── metadata.yaml        # 插件元数据
```

### 核心组件

- **ConflictDetectionManager**: 负责指令冲突检测和Bot选择
- **CommandRecord**: 指令记录数据模型
- **ConflictEvent**: 冲突事件记录
- **PluginConfig**: 配置模型

## 📝 更新日志

### v1.0.0 (2026-04-14)
- ✨ 初始版本发布
- ✨ 支持时间窗口冲突检测
- ✨ 支持三种Bot选择策略
- ✨ 支持详细的日志记录和统计
- ✨ 支持WebUI可视化配置

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 📄 许可证

本项目采用 [MIT](LICENSE) 许可证。

## 🔗 相关链接

- [AstrBot 官方仓库](https://github.com/AstrBotDevs/AstrBot)
- [AstrBot 插件开发文档](https://docs.astrbot.app/dev/star/plugin-new.html)
- [AstrBot 插件市场](https://github.com/AstrBotDevs/AstrBot_Plugins)

---

**Made with ❤️ by yaohewoma**