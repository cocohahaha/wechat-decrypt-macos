# wechat-decrypt-macos

[![Python](https://img.shields.io/badge/Python-3.9+-blue.svg)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-macOS-lightgrey.svg)](https://github.com/cocohahaha/wechat-decrypt-macos)

> 微信 macOS 聊天记录解密、备份与分析工具 — 通过进程内存提取 SQLCipher 密钥，解密本地数据库

**关键词**: 微信解密 / WeChat Decrypt / 聊天记录备份 / 聊天记录导出 / macOS / SQLCipher / 密钥提取 / 内存取证 / 微信数据库 / WCDB

## 功能特性

- 🔑 从微信进程内存自动提取 SQLCipher 加密密钥
- 🔓 解密并查询微信本地聊天数据库 (message_*.db)
- 📊 聊天内容分析 — 消息类型、活跃度、时间分布、情感倾向、高频词
- 📤 导出聊天记录为 JSON / CSV
- 🖥️ 支持 Apple Silicon (M1/M2/M3/M4) 和 Intel Mac
- 🐍 提供 CLI 命令行工具和 Python API 两种使用方式

## 工作原理

微信 macOS 版使用 SQLCipher 加密本地聊天数据库。本工具通过以下步骤实现解密：

1. **重签名** — 移除微信的 Hardened Runtime 保护，添加 `get-task-allow` 权限
2. **内存搜索** — 通过 Mach VM API 读取微信进程内存，定位数据库加密密钥
3. **解密查询** — 使用提取的密钥通过 sqlcipher 查询加密数据库

## 前置条件

- macOS (Apple Silicon / Intel)
- Python >= 3.9
- sqlcipher: `brew install sqlcipher`
- 微信已安装并登录

## 安装

```bash
pip install .
```

或直接从源码使用：

```bash
git clone https://github.com/cocohahaha/wechat-decrypt-macos.git
cd wechat-decrypt-macos
pip install -e .
```

## 快速开始

### 1. 检查环境

```bash
wechat-decrypt check
```

### 2. 重签名微信 (首次使用 / 微信更新后)

```bash
# 先退出微信
sudo wechat-decrypt resign
# 重新启动微信并登录
```

### 3. 提取密钥

```bash
sudo wechat-decrypt extract-key --save key.txt
```

### 4. 分析聊天

```bash
wechat-decrypt analyze --key $(cat key.txt)
wechat-decrypt analyze --key $(cat key.txt) --days 7 --json
```

### 5. 导出聊天记录

```bash
wechat-decrypt export messages.json --key $(cat key.txt) --days 30
wechat-decrypt export messages.csv --key $(cat key.txt) --format csv
```

## 所有命令

| 命令 | 说明 | 需要 sudo |
|------|------|-----------|
| `check` | 检查前置条件 | 否 |
| `resign` | 重签名微信 | 是 |
| `extract-key` | 提取加密密钥 | 是 |
| `query` | 执行 SQL 查询 | 否 |
| `list-chats` | 列出所有对话 | 否 |
| `analyze` | 分析聊天内容 | 否 |
| `export` | 导出聊天记录 | 否 |

## Python API

```python
from wechat_decrypt import extract_key, WeChatDB, analyze_chats, format_report

# 提取密钥 (需要 sudo)
key = extract_key()

# 查询数据库
db = WeChatDB(key_hex=key)
messages = db.get_all_recent_messages(days=7)

# 分析
analysis = analyze_chats(db, days=30)
print(format_report(analysis))

# 导出
db.export_messages("output.json", days=30, fmt="json")
```

## 技术细节

- **加密**: SQLCipher 4, `cipher_page_size=4096`, `kdf_iter=256000`
- **密钥提取**: 在进程内存中搜索数据库 salt (前 16 字节) → 定位 `codec_ctx` 结构体 → 跟踪指针到 `cipher_ctx` → 提取 32 字节派生密钥
- **消息表**: `Msg_<md5(contact_wxid)>`, 通过 `Name2Id` 表反查联系人
- **内存读取**: 使用 macOS Mach VM API (`task_for_pid`, `mach_vm_read_overwrite`)

## 常见问题

**Q: task_for_pid 失败？**
确保: (1) 使用 sudo 运行 (2) 微信已重签名 (3) 微信正在运行

**Q: 微信更新后密钥提取失败？**
微信更新会覆盖重签名，需要重新执行 `sudo wechat-decrypt resign`

**Q: 密钥提取后可以正常使用微信吗？**
可以。密钥提取只是读取内存，不会修改微信运行状态。

## 兼容性

| 项目 | 支持 |
|------|------|
| macOS 版本 | Ventura 13+ / Sonoma 14+ / Sequoia 15+ |
| 芯片架构 | Apple Silicon (M1/M2/M3/M4) / Intel |
| 微信版本 | macOS 微信 (新版 WCDB 格式) |
| Python | 3.9+ |

## Star History

如果觉得有用，请给个 Star ⭐ 让更多人看到！

## 免责声明

本工具仅用于备份和分析**自己的**聊天记录。请遵守当地法律法规，不要用于未经授权的数据访问。

## License

MIT
