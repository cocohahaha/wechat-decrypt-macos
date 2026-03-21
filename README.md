# wechat-decrypt-macos

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-macOS-lightgrey.svg)](https://github.com/cocohahaha/wechat-decrypt-macos)
[![MCP](https://img.shields.io/badge/MCP-Compatible-purple.svg)](https://modelcontextprotocol.io)

> 微信 macOS 聊天记录解密与分析 MCP Server — 让 AI 直接读取和分析你的微信聊天记录

**关键词**: 微信解密 / WeChat Decrypt / MCP Server / Claude Code / 聊天记录分析 / macOS / SQLCipher / 密钥提取

## v2.0 — MCP Server 版本

**全新架构**：从 CLI 工具升级为 [MCP (Model Context Protocol)](https://modelcontextprotocol.io) Server。现在你可以直接在 Claude Code 中对话式地查询和分析微信聊天记录，无需手动输入命令。

### 功能

- 🔑 从微信进程内存自动提取 SQLCipher 加密密钥
- 🔓 解密并查询微信本地聊天数据库 (message_*.db, contact.db)
- 💬 列出对话、读取消息、搜索关键词、获取最近消息
- 📊 聊天分析 — 消息统计、活跃度、时间分布
- 🤖 MCP 协议 — 可直接被 Claude Code、Cursor 等 AI 工具调用

## 前置条件

- macOS (Apple Silicon / Intel)
- Python >= 3.10
- sqlcipher: `brew install sqlcipher`
- 微信已安装并登录

## 安装

```bash
git clone https://github.com/cocohahaha/wechat-decrypt-macos.git
cd wechat-decrypt-macos
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## 提取密钥（首次使用）

微信使用 SQLCipher 加密本地数据库。需要先提取密钥：

```bash
# 1. 重签名微信（移除 Hardened Runtime，首次使用/微信更新后需要）
sudo python -c "from server import *; print('请参考 wechat-decrypt-macos 旧版文档进行重签名')"

# 2. 提取密钥并保存
# 密钥会自动保存到 key.txt
```

## 配置 Claude Code

在 `~/.claude/settings.json` 中添加：

```json
{
  "mcpServers": {
    "wechat": {
      "command": "/path/to/wechat-decrypt-macos/.venv/bin/python",
      "args": ["/path/to/wechat-decrypt-macos/server.py"]
    }
  }
}
```

## 使用方式

配置完成后，直接在 Claude Code 中用自然语言：

```
你：帮我看看最近和张三聊了什么
Claude：(自动调用 MCP 工具读取微信数据库，返回聊天摘要)

你：搜索一下我和李四聊天记录中提到"项目"的内容
Claude：(搜索并返回相关消息)

你：分析一下我最活跃的 5 个聊天对话
Claude：(查询数据库，返回统计分析)
```

## 技术细节

- **架构**: FastMCP Server (单文件 `server.py`, ~440 行)
- **加密**: SQLCipher 4, `cipher_page_size=4096`, `cipher_compatibility=4`
- **密钥提取**: 在进程内存中搜索数据库 salt → 定位 `codec_ctx` → 提取 32 字节派生密钥
- **协议**: [Model Context Protocol](https://modelcontextprotocol.io)
- **内存读取**: macOS Mach VM API (`task_for_pid`, `mach_vm_read_overwrite`)

## 兼容性

| 项目 | 支持 |
|------|------|
| macOS 版本 | Ventura 13+ / Sonoma 14+ / Sequoia 15+ |
| 芯片架构 | Apple Silicon (M1/M2/M3/M4) / Intel |
| 微信版本 | macOS 微信 (新版 WCDB 格式) |
| Python | 3.10+ |
| AI 工具 | Claude Code, Cursor, 及任何支持 MCP 的客户端 |

## Star History

如果觉得有用，请给个 Star ⭐ 让更多人看到！

## 免责声明

本工具仅用于备份和分析**自己的**聊天记录。请遵守当地法律法规，不要用于未经授权的数据访问。

## License

MIT
