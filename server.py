"""WeChat MCP Server - Read and analyze WeChat chat history on macOS."""

import csv
import glob
import hashlib
import io
import json
import os
import subprocess
import time
from datetime import datetime

from mcp.server.fastmcp import FastMCP

# --- Configuration ---
KEY_FILE = os.path.join(os.path.dirname(__file__), "key.txt")
SQLCIPHER_PATH = "/opt/homebrew/bin/sqlcipher"
CONTACTS_FILE = os.path.join(os.path.dirname(__file__), "contacts.json")

mcp = FastMCP(
    "wechat",
    instructions=(
        "WeChat 聊天记录读取与分析工具。可以列出聊天对话、读取消息内容、搜索关键词、"
        "获取最近消息。用于分析用户的微信聊天记录，提取待办事项和行动项。\n"
        "消息中 [我] 表示用户自己发的，[对方] 表示联系人发的。"
    ),
)


# --- Contact info cache ---

_contacts_cache: dict[str, dict] | None = None


def _load_contacts() -> dict[str, dict]:
    """Load contacts mapping from contacts.json.

    File format:
    {
        "wxid_ge83frr86ypp22": {"nickname": "蔡蔡", "remark": ""},
        "jingjingleaf": {"nickname": "张老师", "remark": "Mika铁"}
    }
    """
    global _contacts_cache
    if _contacts_cache is not None:
        return _contacts_cache
    if os.path.exists(CONTACTS_FILE):
        try:
            with open(CONTACTS_FILE, "r", encoding="utf-8") as f:
                _contacts_cache = json.load(f)
        except (json.JSONDecodeError, OSError):
            _contacts_cache = {}
    else:
        _contacts_cache = {}
    return _contacts_cache


def _get_contact_info(wxid: str) -> dict:
    """Get nickname and remark for a wxid from contacts cache.

    Returns dict with keys: nickname, remark (may be empty strings).
    """
    contacts = _load_contacts()
    return contacts.get(wxid, {"nickname": "", "remark": ""})


def _resolve_contact_name(wxid: str) -> str:
    """Get a human-readable display name for a wxid.

    Priority: remark > nickname > wxid.
    For group chats, shows a friendlier format.
    """
    if "@chatroom" in wxid:
        info = _get_contact_info(wxid)
        if info.get("remark"):
            return info["remark"]
        if info.get("nickname"):
            return info["nickname"]
        return f"群聊({wxid.split('@')[0]})"

    info = _get_contact_info(wxid)
    if info.get("remark"):
        return info["remark"]
    if info.get("nickname"):
        return info["nickname"]
    return wxid


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
        stdout = result.stdout.decode().strip()
        stderr = result.stderr.decode().strip()
        # Check stderr for errors (sqlcipher puts parse errors there)
        if "error" in stderr.lower():
            return False
        # Check stdout: should have a numeric count after "ok"
        lines = [l.strip() for l in stdout.split("\n") if l.strip() and l.strip() != "ok"]
        return any(l.isdigit() and int(l) > 0 for l in lines)
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
    dbs = sorted(glob.glob(pattern))
    # Only return dbs that are accessible with our key
    key = _load_key()
    return [db for db in dbs if _test_key(key, db)]


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


def _detect_my_sender_id(db_path: str) -> int | None:
    """Detect the real_sender_id that represents 'me' (the account owner).

    The 'me' sender_id appears across ALL chat tables.
    We sample a few Msg_ tables and find the common real_sender_id.
    """
    tables_raw = _query_raw(
        db_path, "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%';"
    )
    msg_tables = [t.strip() for t in tables_raw if t.strip().startswith("Msg_")]

    if not msg_tables:
        return None

    # Sample up to 5 tables
    sample = msg_tables[:5]
    sender_sets = []
    for table in sample:
        rows = _query(
            db_path,
            f"SELECT DISTINCT real_sender_id FROM {table} "
            f"WHERE local_type NOT IN (10000, 10002) LIMIT 20;",
        )
        ids = {int(r.get("real_sender_id", 0)) for r in rows if r.get("real_sender_id")}
        if ids:
            sender_sets.append(ids)

    if not sender_sets:
        return None

    # Find the sender_id present in ALL sampled tables
    common = sender_sets[0]
    for s in sender_sets[1:]:
        common = common & s

    # Remove 0 and system-like IDs
    common.discard(0)

    if len(common) == 1:
        return common.pop()

    # If multiple common IDs, return the smallest non-zero one (heuristic)
    if common:
        return min(common)

    return None


_my_sender_id_cache: int | None = None
_my_sender_id_detected: bool = False


def _get_my_sender_id() -> int | None:
    """Get the cached 'my' sender_id, detecting on first call."""
    global _my_sender_id_cache, _my_sender_id_detected
    if _my_sender_id_detected:
        return _my_sender_id_cache
    dbs = _get_message_dbs()
    if dbs:
        _my_sender_id_cache = _detect_my_sender_id(dbs[0])
    _my_sender_id_detected = True
    return _my_sender_id_cache


def _is_my_message(real_sender_id: str | int) -> bool:
    """Check if a message was sent by 'me' based on real_sender_id."""
    my_id = _get_my_sender_id()
    if my_id is None:
        return False
    try:
        return int(real_sender_id) == my_id
    except (ValueError, TypeError):
        return False


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


def _find_contact(contact: str, name2id: dict[str, str]) -> list[tuple[str, str, str]]:
    """Find matching contacts by wxid, nickname, or remark.

    Returns list of (table_name, wxid, display_name) tuples.
    Matching priority:
    1. Exact wxid match
    2. Partial wxid match
    3. Nickname match (fuzzy)
    4. Remark match (fuzzy)
    """
    contact_lower = contact.lower()
    contacts_map = _load_contacts()

    # Phase 1: Exact wxid match
    exact = []
    for table, wxid in name2id.items():
        if wxid.lower() == contact_lower:
            display = _resolve_contact_name(wxid)
            exact.append((table, wxid, display))
    if exact:
        return exact

    # Phase 2: Partial wxid match
    partial_wxid = []
    for table, wxid in name2id.items():
        if contact_lower in wxid.lower():
            display = _resolve_contact_name(wxid)
            partial_wxid.append((table, wxid, display))

    # Phase 3: Nickname match (fuzzy)
    nickname_match = []
    for table, wxid in name2id.items():
        info = contacts_map.get(wxid, {})
        nickname = info.get("nickname", "")
        if nickname and contact_lower in nickname.lower():
            display = _resolve_contact_name(wxid)
            nickname_match.append((table, wxid, display))

    # Phase 4: Remark match (fuzzy)
    remark_match = []
    for table, wxid in name2id.items():
        info = contacts_map.get(wxid, {})
        remark = info.get("remark", "")
        if remark and contact_lower in remark.lower():
            display = _resolve_contact_name(wxid)
            remark_match.append((table, wxid, display))

    # Combine results, deduplicate
    seen = set()
    results = []
    for match_list in [partial_wxid, nickname_match, remark_match]:
        for item in match_list:
            if item[1] not in seen:
                seen.add(item[1])
                results.append(item)

    return results


# --- MCP Tools ---

@mcp.tool()
def wechat_list_chats() -> str:
    """列出所有微信聊天对话。返回联系人/群聊列表及其标识符。

    显示格式：昵称 (备注: xxx) (wxid: xxx)
    用于了解用户有哪些对话，之后可以用 wechat_read_chat 读取具体对话内容。
    支持通过昵称、备注名或 wxid 搜索联系人。"""
    name2id = _get_name2id()
    if not name2id:
        return "未找到任何对话记录"

    contacts_map = _load_contacts()
    lines = [f"共 {len(name2id)} 个对话:\n"]

    for table, wxid in sorted(name2id.items(), key=lambda x: x[1]):
        info = contacts_map.get(wxid, {})
        nickname = info.get("nickname", "")
        remark = info.get("remark", "")

        if nickname:
            remark_part = f" (备注: {remark})" if remark else " (备注: 无)"
            lines.append(f"  {nickname}{remark_part} (wxid: {wxid})")
        elif "@chatroom" in wxid:
            group_name = remark or nickname or wxid.split("@")[0]
            lines.append(f"  群聊({group_name}) (wxid: {wxid})")
        else:
            lines.append(f"  {wxid}")

    has_contacts = bool(contacts_map)
    if not has_contacts:
        lines.append(
            f"\n提示: 联系人昵称/备注未配置。"
            f"请编辑 {CONTACTS_FILE} 添加联系人信息。"
            f'\n格式: {{"wxid_xxx": {{"nickname": "昵称", "remark": "备注"}}, ...}}'
        )

    return "\n".join(lines)


@mcp.tool()
def wechat_read_chat(contact: str, limit: int = 50, days: int = 7) -> str:
    """读取与指定联系人的聊天记录。

    消息中 [我] 表示用户自己发的，[对方] 表示联系人发的。

    Args:
        contact: 联系人的 wxid、昵称或备注名（支持模糊匹配）
        limit: 返回的最大消息数量，默认50
        days: 读取最近几天的消息，默认7天
    """
    name2id = _get_name2id()
    since = int(time.time()) - days * 86400

    # Find matching contacts
    matched = _find_contact(contact, name2id)

    if not matched:
        return f"未找到匹配 '{contact}' 的联系人。请用 wechat_list_chats 查看所有对话。"

    # If too many matches, ask user to be more specific
    if len(matched) > 5:
        lines = [f"匹配 '{contact}' 的联系人太多 ({len(matched)} 个)，请更精确:\n"]
        for _, wxid, display in matched[:10]:
            lines.append(f"  {display} (wxid: {wxid})")
        if len(matched) > 10:
            lines.append(f"  ... 还有 {len(matched) - 10} 个")
        return "\n".join(lines)

    results = []
    for table, wxid, display in matched:
        results.append(f"\n=== 与 {display} 的对话 (wxid: {wxid}) ===\n")

        for db_path in _get_message_dbs():
            # Check if table exists in this db
            tables = _query_raw(db_path, "SELECT name FROM sqlite_master WHERE type='table';")
            if table not in [t.strip() for t in tables]:
                continue

            rows = _query(
                db_path,
                f"SELECT local_id, create_time, local_type, real_sender_id, message_content "
                f"FROM {table} WHERE create_time > {since} "
                f"ORDER BY create_time DESC LIMIT {limit};",
            )
            for row in reversed(rows):  # Show oldest first
                ts = _format_time(row.get("create_time", "0"))
                raw_type = row.get("local_type", "")
                # Normalize large local_type values (WeChat Mac uses high bits for subtypes)
                try:
                    type_key = str(int(raw_type) & 0xFFFF) if raw_type else ""
                except (ValueError, TypeError):
                    type_key = raw_type
                msg_type = MSG_TYPES.get(type_key, "其他")
                content = row.get("message_content", "") or ""
                sender_id = row.get("real_sender_id", "")

                # Determine direction
                is_me = _is_my_message(sender_id)
                direction = "[我]" if is_me else "[对方]"

                # Skip system messages
                if type_key in ("10000", "10002"):
                    results.append(f"  [{ts}] [系统] {msg_type}")
                    continue

                # Skip XML/binary for non-text messages
                if content.startswith("<") and type_key != "1":
                    results.append(f"  [{ts}] {direction} [{msg_type}]")
                elif content.startswith("\x08") or (len(content) > 0 and ord(content[0]) > 127 and type_key != "1"):
                    results.append(f"  [{ts}] {direction} [{msg_type}]")
                else:
                    content_preview = content[:200].replace("\n", " ")
                    results.append(f"  [{ts}] {direction} {content_preview}")

    return "\n".join(results) if results else "未找到消息"


@mcp.tool()
def wechat_recent_messages(days: int = 3, limit: int = 100) -> str:
    """获取最近几天所有对话的消息概览。

    消息中 [我] 表示用户自己发的，[对方] 表示联系人发的。

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
                f"SELECT create_time, local_type, real_sender_id, message_content "
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
            raw_type = m.get("local_type", "")
            try:
                type_key = str(int(raw_type) & 0xFFFF) if raw_type else ""
            except (ValueError, TypeError):
                type_key = raw_type
            sender_id = m.get("real_sender_id", "")
            is_me = _is_my_message(sender_id)
            direction = "[我]" if is_me else "[对方]"

            # Only show text messages for action item analysis
            if type_key == "1" and not content.startswith("<"):
                ts = _format_time(m.get("create_time", "0"))
                content_preview = content[:300].replace("\n", " ")
                lines.append(f"  [{ts}] {direction} {content_preview}")
                text_shown += 1
            elif type_key in ("3", "34", "43", "49"):
                ts = _format_time(m.get("create_time", "0"))
                type_name = MSG_TYPES.get(type_key, "其他")
                lines.append(f"  [{ts}] {direction} [{type_name}]")
                text_shown += 1

    return "\n".join(lines)


@mcp.tool()
def wechat_search_messages(keyword: str, days: int = 30, limit: int = 50) -> str:
    """在聊天记录中搜索包含关键词的消息。

    消息中 [我] 表示用户自己发的，[对方] 表示联系人发的。

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
                f"SELECT create_time, local_type, real_sender_id, message_content "
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
        sender_id = m.get("real_sender_id", "")
        is_me = _is_my_message(sender_id)
        direction = "[我]" if is_me else "[对方]"
        lines.append(f"  [{ts}] {contact} {direction}: {content}")

    return "\n".join(lines)


@mcp.tool()
def wechat_chat_summary(days: int = 3) -> str:
    """生成最近聊天的结构化摘要，方便 AI 分析用户接下来需要做什么。

    返回每个对话的最新消息，按时间排序，标注消息类型。
    消息中 [我] 表示用户自己发的，[对方] 表示联系人发的。

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
                f"SELECT create_time, local_type, real_sender_id, message_content "
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
        "消息标记说明: [我] = 用户自己发的, [对方] = 联系人发的\n",
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
            sender_id = m.get("real_sender_id", "")
            is_me = _is_my_message(sender_id)
            direction = "[我]" if is_me else "[对方]"
            lines.append(f"  [{ts}] {direction} {content}")

    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
