"""Analyze decrypted WeChat chat messages."""

from collections import Counter
from datetime import datetime

from .db_reader import WeChatDB

MSG_TYPE_NAMES = {
    "1": "文本", "3": "图片", "34": "语音", "42": "名片",
    "43": "视频", "47": "表情", "48": "位置", "49": "链接/文件",
    "50": "通话", "10000": "系统消息", "10002": "撤回",
}

POSITIVE_WORDS = [
    "哈哈", "谢谢", "开心", "不错", "好的", "太好", "喜欢", "厉害",
    "棒", "赞", "感谢", "666", "牛", "哈哈哈", "笑死", "优秀",
    "加油", "恭喜",
]
NEGATIVE_WORDS = [
    "唉", "烦", "无聊", "讨厌", "生气", "难过", "失望", "算了",
    "郁闷", "累", "难受", "崩溃", "焦虑", "纠结", "不想",
]


def analyze_chats(db, days=30):
    """Analyze recent chat messages.

    Args:
        db: WeChatDB instance
        days: Number of days to analyze

    Returns:
        Analysis result dict.
    """
    msgs = db.get_all_recent_messages(days=days, limit_per_table=200)
    name2id = db.get_name2id()

    # Type distribution
    type_counter = Counter()
    for m in msgs:
        t = m.get("local_type", "?")
        type_counter[MSG_TYPE_NAMES.get(t, f"类型{t}")] += 1

    # Conversation activity
    chat_counter = Counter()
    for m in msgs:
        table = m.get("_table", "?")
        name = name2id.get(table, table)
        chat_counter[name] += 1

    # Time distributions
    date_counter = Counter()
    hour_counter = Counter()
    for m in msgs:
        ts = int(m.get("create_time", "0") or "0")
        if ts > 0:
            dt = datetime.fromtimestamp(ts)
            date_counter[dt.strftime("%Y-%m-%d")] += 1
            hour_counter[dt.hour] += 1

    # Group vs private
    group_msgs = sum(
        c for name, c in chat_counter.items() if "@chatroom" in name
    )
    private_msgs = sum(
        c for name, c in chat_counter.items() if "@chatroom" not in name
    )

    # Text analysis
    text_msgs = [
        m for m in msgs
        if m.get("local_type") == "1"
        and not (m.get("message_content") or "").startswith("<")
    ]

    bigrams = Counter()
    for m in text_msgs:
        content = m.get("message_content", "") or ""
        for i in range(len(content) - 1):
            pair = content[i : i + 2]
            if all("\u4e00" <= c <= "\u9fff" for c in pair):
                bigrams[pair] += 1

    # Sentiment
    pos = neg = neu = 0
    for m in text_msgs:
        content = m.get("message_content", "") or ""
        has_pos = any(w in content for w in POSITIVE_WORDS)
        has_neg = any(w in content for w in NEGATIVE_WORDS)
        if has_pos and not has_neg:
            pos += 1
        elif has_neg:
            neg += 1
        else:
            neu += 1

    # Recent preview
    preview = []
    for m in msgs:
        if m.get("local_type") != "1":
            continue
        content = m.get("message_content", "") or ""
        if content.startswith("<") or not content.strip():
            continue
        ts = int(m.get("create_time", "0") or "0")
        table = m.get("_table", "")
        preview.append({
            "time": datetime.fromtimestamp(ts).strftime("%m-%d %H:%M") if ts else "?",
            "contact": name2id.get(table, table),
            "content": content[:120],
        })
        if len(preview) >= 20:
            break

    return {
        "total_messages": len(msgs),
        "days": days,
        "type_distribution": dict(type_counter.most_common(15)),
        "top_conversations": dict(chat_counter.most_common(20)),
        "date_distribution": dict(sorted(date_counter.items())),
        "hour_distribution": {h: hour_counter.get(h, 0) for h in range(24)},
        "group_vs_private": {"群聊": group_msgs, "私聊": private_msgs},
        "text_count": len(text_msgs),
        "top_bigrams": bigrams.most_common(30),
        "sentiment": {"积极": pos, "消极": neg, "中性": neu},
        "recent_preview": preview,
    }


def format_report(analysis):
    """Format analysis results into a readable Chinese report."""
    lines = []
    a = analysis
    lines.append("=" * 60)
    lines.append("       WeChat 聊天内容分析报告")
    lines.append(f"       分析时间范围: 最近{a['days']}天")
    lines.append(f"       消息总数: {a['total_messages']}")
    lines.append("=" * 60)

    # Type distribution
    lines.append("\n【消息类型分布】")
    for t, count in a["type_distribution"].items():
        lines.append(f"  {t}: {count} 条")

    # Top conversations
    lines.append("\n【最活跃的对话】")
    for name, count in a["top_conversations"].items():
        lines.append(f"  {name}: {count} 条")

    # Date distribution
    lines.append("\n【消息时间分布 - 按天】")
    dates = a["date_distribution"]
    max_d = max(dates.values()) if dates else 1
    for date in list(dates)[-14:]:
        count = dates[date]
        bar = "\u2593" * min(count * 40 // max(max_d, 1), 40)
        lines.append(f"  {date}: {bar} {count}")

    # Hour distribution
    lines.append("\n【消息时间分布 - 按小时】")
    hours = a["hour_distribution"]
    max_h = max(hours.values()) if hours else 1
    for h in range(24):
        c = hours.get(h, 0)
        bar = "\u2593" * (c * 30 // max(max_h, 1))
        lines.append(f"  {h:02d}:00 {bar} {c}")

    # Group vs private
    lines.append("\n【群聊 vs 私聊】")
    for k, v in a["group_vs_private"].items():
        lines.append(f"  {k}: {v} 条")

    # Bigrams
    lines.append(f"\n【高频关键词】(文本消息 {a['text_count']} 条)")
    for word, count in a["top_bigrams"]:
        lines.append(f"  {word}: {count}")

    # Sentiment
    lines.append("\n【情感倾向】")
    s = a["sentiment"]
    total_s = s["积极"] + s["消极"] + s["中性"]
    if total_s > 0:
        for label in ("积极", "消极", "中性"):
            pct = s[label] * 100 // total_s
            lines.append(f"  {label}: {s[label]} ({pct}%)")

    # Recent preview
    lines.append("\n【最近消息预览】")
    for p in a["recent_preview"]:
        content = p["content"].replace("\n", " ")
        lines.append(f"  [{p['time']}] {p['contact']}: {content}")

    return "\n".join(lines)
