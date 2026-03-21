"""WeChat MCP Server - Read and analyze WeChat chat history on macOS."""

import csv
import glob
import hashlib
import io
import os
import subprocess
import time
from datetime import datetime

from mcp.server.fastmcp import FastMCP

# --- Configuration ---
KEY_FILE = os.path.join(os.path.dirname(__file__), "..", "wechat-decrypt-macos", "key.txt")
SQLCIPHER_PATH = "/opt/homebrew/bin/sqlcipher"

mcp = FastMCP(
    "wechat",
    instructions=(
        "WeChat 聊天记录读取与分析工具。可以列出聊天对话、读取消息内容、搜索关键词、"
        "获取最近消息。用于分析用户的微信聊天记录，提取待办事项和行动项。"
    ),
)


# --- Database helpers ---

def _load_key() -> str:
    """Load the SQLCipher key from key.txt."""
    if not os.path.exists(KEY_FILE):
        raise FileNotFoundError(
            f"密钥文件不存在: {KEY_FILE}\n"
            "请先运行 wechat-decrypt extract-key --save key.txt 提取密钥"
        )
    return open(KEY_FILE).read().strip()


def _find_data_dir() -> str:
    """Find the WeChat db_storage directory."""
    pattern = os.path.expanduser(
        "~/Library/Containers/com.tencent.xinWeChat/Data/Documents/"
        "xwechat_files/*/db_storage/message/message_0.db"
    )
    matches = glob.glob(pattern)
    if not matches:
        raise FileNotFoundError("未找到 WeChat 数据目录")
    # Use the key to find the right account
    key = _load_key()
    for match in sorted(matches, key=os.path.getmtime, reverse=True):
        db_dir = os.path.dirname(os.path.dirname(match))
        if _test_key(key, match):
            return db_dir
    # Fallback to most recent
    best = max(matches, key=os.path.getmtime)
    return os.path.dirname(os.path.dirname(best))


def _test_key(key: str, db_path: str) -> bool:
    """Test if a key works on a database."""
    cmd = (
        f"PRAGMA key = \"x'{key}'\";\n"
        "PRAGMA cipher_compatibility = 4;\n"
        "PRAGMA cipher_page_size = 4096;\n"
        "SELECT count(*) FROM sqlite_master;\n"
    )
    try:
        result = subprocess.run(
            [SQLCIPHER_PATH, db_path],
            input=cmd.encode(), capture_output=True, timeout=5,
        )
        output = result.stdout.decode().strip()
        return "error" not in output.lower() or output.split("\n")[-1].strip().isdigit()
    except Exception:
        return False


def _preamble(key: str) -> str:
    return (
        f"PRAGMA key = \"x'{key}'\";\n"
        "PRAGMA cipher_compatibility = 4;\n"
        "PRAGMA cipher_page_size = 4096;\n"
    )


def _query(db_path: str, sql: str) -> list[dict]:
    """Execute SQL query and return list of dicts."""
    key = _load_key()
    cmd = _preamble(key) + ".headers on\n.mode csv\n" + sql
    result = subprocess.run(
        [SQLCIPHER_PATH, db_path],
        input=cmd.encode(), capture_output=True, timeout=30,
    )
    text = result.stdout.decode("utf-8", errors="replace").strip()
    if not text:
        return []
    lines = text.split("\n")
    while lines and lines[0].strip() == "ok":
        lines.pop(0)
    if len(lines) < 2:
        return []
    return list(csv.DictReader(io.StringIO("\n".join(lines))))


def _query_raw(db_path: str, sql: str) -> list[str]:
    """Execute SQL and return raw output lines."""
    key = _load_key()
    cmd = _preamble(key) + sql
    result = subprocess.run(
        [SQLCIPHER_PATH, db_path],
        input=cmd.encode(), capture_output=True, timeout=30,
    )
    text = result.stdout.decode("utf-8", errors="replace").strip()
    return [l for l in text.split("\n") if l.strip() and l.strip() != "ok"]


def _get_message_dbs() -> list[str]:
    """Get paths to all message database files."""
    data_dir = _find_data_dir()
    pattern = os.path.join(data_dir, "message", "message_[0-9].db")
    return sorted(glob.glob(pattern))


def _get_name2id() -> dict[str, str]:
    """Build mapping from Msg_ table name to username/wxid."""
    dbs = _get_message_dbs()
    if not dbs:
        return {}
    rows = _query(dbs[0], "SELECT user_name FROM Name2Id;")
    mapping = {}
    for row in rows:
        un = row.get("user_name", "")
        if un:
            h = hashlib.md5(un.encode()).hexdigest()
            mapping[f"Msg_{h}"] = un
    return mapping


def _resolve_contact_name(wxid: str) -> str:
    """Try to get a human-readable name for a wxid."""
    # For group chats
    if "@chatroom" in wxid:
        return f"群聊({wxid.split('@')[0]})"
    # For regular contacts, return wxid as-is (no contact DB access yet)
    return wxid


def _format_time(ts: str | int) -> str:
    """Format unix timestamp to readable string."""
    try:
        t = int(ts)
        if t > 0:
            return datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError, OSError):
        pass
    return "未知时间"


MSG_TYPES = {
    "1": "文本", "3": "图片", "34": "语音", "42": "名片",
    "43": "视频", "47": "表情", "48": "位置", "49": "链接/文件",
    "50": "通话", "10000": "系统消息", "10002": "撤回",
}


# --- MCP Tools ---

@mcp.tool()
def wechat_list_chats() -> str:
    """列出所有微信聊天对话。返回联系人/群聊列表及其标识符。
    用于了解用户有哪些对话，之后可以用 wechat_read_chat 读取具体对话内容。"""
    name2id = _get_name2id()
    if not name2id:
        return "未找到任何对话记录"

    lines = [f"共 {len(name2id)} 个对话:\n"]
    for table, username in sorted(name2id.items(), key=lambda x: x[1]):
        display = _resolve_contact_name(username)
        lines.append(f"  {display}  (wxid: {username})")

    return "\n".join(lines)


@mcp.tool()
def wechat_read_chat(contact: str, limit: int = 50, days: int = 7) -> str:
    """读取与指定联系人的聊天记录。

    Args:
        contact: 联系人的 wxid 或用户名（支持部分匹配）
        limit: 返回的最大消息数量，默认50
        days: 读取最近几天的消息，默认7天
    """
    name2id = _get_name2id()
    since = int(time.time()) - days * 86400

    # Find matching contact
    matched_tables = []
    for table, username in name2id.items():
        if contact.lower() in username.lower():
            matched_tables.append((table, username))

    if not matched_tables:
        return f"未找到匹配 '{contact}' 的联系人。请用 wechat_list_chats 查看所有对话。"

    results = []
    for table, username in matched_tables:
        display = _resolve_contact_name(username)
        results.append(f"\n=== 与 {display} 的对话 ===\n")

        for db_path in _get_message_dbs():
            # Check if table exists in this db
            tables = _query_raw(db_path, "SELECT name FROM sqlite_master WHERE type='table';")
            if table not in [t.strip() for t in tables]:
                continue

            rows = _query(
                db_path,
                f"SELECT local_id, create_time, local_type, message_content "
                f"FROM {table} WHERE create_time > {since} "
                f"ORDER BY create_time DESC LIMIT {limit};",
            )
            for row in reversed(rows):  # Show oldest first
                ts = _format_time(row.get("create_time", "0"))
                msg_type = MSG_TYPES.get(row.get("local_type", ""), "其他")
                content = row.get("message_content", "") or ""

                # Skip XML/system messages for readability
                if content.startswith("<") and msg_type != "文本":
                    results.append(f"  [{ts}] [{msg_type}]")
                else:
                    content_preview = content[:200].replace("\n", " ")
                    results.append(f"  [{ts}] {content_preview}")

    return "\n".join(results) if results else "未找到消息"


@mcp.tool()
def wechat_recent_messages(days: int = 3, limit: int = 100) -> str:
    """获取最近几天所有对话的消息概览。

    适合用于：
    - 快速了解用户最近的聊天动态
    - 提取待办事项和行动项
    - 分析用户接下来需要做什么

    Args:
        days: 获取最近几天的消息，默认3天
        limit: 每个对话最多返回的消息数，默认100
    """
    name2id = _get_name2id()
    since = int(time.time()) - days * 86400
    all_msgs = []

    for db_path in _get_message_dbs():
        tables_raw = _query_raw(db_path, "SELECT name FROM sqlite_master WHERE type='table';")
        msg_tables = [t.strip() for t in tables_raw if t.strip().startswith("Msg_")]

        for table in msg_tables:
            rows = _query(
                db_path,
                f"SELECT create_time, local_type, message_content "
                f"FROM {table} WHERE create_time > {since} "
                f"ORDER BY create_time DESC LIMIT {limit};",
            )
            contact = name2id.get(table, table)
            for row in rows:
                row["_contact"] = contact
            all_msgs.extend(rows)

    # Sort by time
    all_msgs.sort(
        key=lambda x: int(x.get("create_time", "0") or "0"),
        reverse=True,
    )

    if not all_msgs:
        return f"最近 {days} 天没有消息"

    # Group by contact for readability
    by_contact: dict[str, list] = {}
    for m in all_msgs:
        c = m["_contact"]
        display = _resolve_contact_name(c)
        if display not in by_contact:
            by_contact[display] = []
        by_contact[display].append(m)

    lines = [f"最近 {days} 天共 {len(all_msgs)} 条消息，涉及 {len(by_contact)} 个对话:\n"]

    for contact, msgs in sorted(by_contact.items(), key=lambda x: -len(x[1])):
        lines.append(f"\n--- {contact} ({len(msgs)} 条) ---")
        # Show text messages only, most recent first
        text_shown = 0
        for m in msgs:
            if text_shown >= 20:
                lines.append(f"  ... 还有更多消息")
                break
            content = m.get("message_content", "") or ""
            msg_type = m.get("local_type", "")

            # Only show text messages for action item analysis
            if msg_type == "1" and not content.startswith("<"):
                ts = _format_time(m.get("create_time", "0"))
                content_preview = content[:300].replace("\n", " ")
                lines.append(f"  [{ts}] {content_preview}")
                text_shown += 1
            elif msg_type in ("3", "34", "43", "49"):
                ts = _format_time(m.get("create_time", "0"))
                type_name = MSG_TYPES.get(msg_type, "其他")
                lines.append(f"  [{ts}] [{type_name}]")
                text_shown += 1

    return "\n".join(lines)


@mcp.tool()
def wechat_search_messages(keyword: str, days: int = 30, limit: int = 50) -> str:
    """在聊天记录中搜索包含关键词的消息。

    Args:
        keyword: 搜索关键词
        days: 搜索最近几天的消息，默认30天
        limit: 最多返回的消息数量，默认50
    """
    name2id = _get_name2id()
    since = int(time.time()) - days * 86400
    results = []

    for db_path in _get_message_dbs():
        tables_raw = _query_raw(db_path, "SELECT name FROM sqlite_master WHERE type='table';")
        msg_tables = [t.strip() for t in tables_raw if t.strip().startswith("Msg_")]

        for table in msg_tables:
            # Use SQL LIKE for search
            safe_keyword = keyword.replace("'", "''")
            rows = _query(
                db_path,
                f"SELECT create_time, local_type, message_content "
                f"FROM {table} "
                f"WHERE create_time > {since} "
                f"AND message_content LIKE '%{safe_keyword}%' "
                f"ORDER BY create_time DESC LIMIT {limit};",
            )
            contact = name2id.get(table, table)
            for row in rows:
                row["_contact"] = contact
            results.extend(rows)

    results.sort(
        key=lambda x: int(x.get("create_time", "0") or "0"),
        reverse=True,
    )

    if not results:
        return f"未找到包含 '{keyword}' 的消息"

    lines = [f"搜索 '{keyword}' 找到 {len(results)} 条消息:\n"]
    for m in results[:limit]:
        ts = _format_time(m.get("create_time", "0"))
        contact = _resolve_contact_name(m.get("_contact", "?"))
        content = (m.get("message_content", "") or "")[:300].replace("\n", " ")
        lines.append(f"  [{ts}] {contact}: {content}")

    return "\n".join(lines)


@mcp.tool()
def wechat_chat_summary(days: int = 3) -> str:
    """生成最近聊天的结构化摘要，方便 AI 分析用户接下来需要做什么。

    返回每个对话的最新消息，按时间排序，标注消息类型。
    AI 应该基于此分析：
    1. 别人对用户提出的请求/问题
    2. 用户答应要做但还没做的事
    3. 约定的时间/地点/计划
    4. 需要回复但还没回复的消息

    Args:
        days: 分析最近几天，默认3天
    """
    name2id = _get_name2id()
    since = int(time.time()) - days * 86400
    conversations = {}

    for db_path in _get_message_dbs():
        tables_raw = _query_raw(db_path, "SELECT name FROM sqlite_master WHERE type='table';")
        msg_tables = [t.strip() for t in tables_raw if t.strip().startswith("Msg_")]

        for table in msg_tables:
            rows = _query(
                db_path,
                f"SELECT create_time, local_type, message_content "
                f"FROM {table} WHERE create_time > {since} "
                f"AND local_type = '1' "
                f"ORDER BY create_time DESC LIMIT 30;",
            )
            if not rows:
                continue
            contact = name2id.get(table, table)
            display = _resolve_contact_name(contact)
            # Filter out XML messages
            text_msgs = [
                r for r in rows
                if r.get("message_content") and not r["message_content"].startswith("<")
            ]
            if text_msgs:
                conversations[display] = text_msgs

    if not conversations:
        return f"最近 {days} 天没有文本消息"

    lines = [
        f"=== 微信聊天摘要（最近 {days} 天）===",
        f"今天是 {datetime.now().strftime('%Y-%m-%d %A')}",
        f"涉及 {len(conversations)} 个对话\n",
        "请分析以下对话，提取：",
        "1. 待办事项（别人请求我做的事）",
        "2. 承诺事项（我答应要做的事）",
        "3. 计划安排（约定的时间/活动）",
        "4. 需要回复的消息",
        "5. 可能需要跟进的事项\n",
    ]

    for contact, msgs in sorted(
        conversations.items(),
        key=lambda x: max(int(m.get("create_time", "0") or "0") for m in x[1]),
        reverse=True,
    ):
        lines.append(f"\n--- {contact} ---")
        for m in reversed(msgs[:20]):  # Show oldest first, max 20
            ts = _format_time(m.get("create_time", "0"))
            content = m["message_content"][:500].replace("\n", " ")
            lines.append(f"  [{ts}] {content}")

    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
