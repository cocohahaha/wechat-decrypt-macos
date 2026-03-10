"""Command-line interface for wechat-decrypt-macos."""

import argparse
import json
import os
import sys

from . import __version__


def cmd_check(args):
    """Check prerequisites."""
    from .utils import check_prerequisites
    issues = check_prerequisites()
    if issues:
        print("发现以下问题:")
        for issue in issues:
            print(f"  - {issue}")
        sys.exit(1)
    else:
        print("所有前置条件满足!")


def cmd_resign(args):
    """Re-sign WeChat."""
    from .resign import resign_wechat, print_resign_guide
    if args.guide:
        print_resign_guide()
        return
    resign_wechat(args.app_path)


def cmd_extract_key(args):
    """Extract encryption key."""
    from .key_extractor import extract_key
    key = extract_key(pid=args.pid, db_path=args.db, verbose=True)
    if args.save:
        with open(args.save, "w") as f:
            f.write(key + "\n")
        print(f"密钥已保存到 {args.save}")
    print(f"\n密钥: {key}")


def cmd_query(args):
    """Execute SQL query."""
    from .db_reader import WeChatDB
    db = WeChatDB(key_hex=args.key, data_dir=args.data_dir)
    rows = db.query(args.db_path, args.sql)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        for row in rows:
            print(row)


def cmd_list_chats(args):
    """List chat contacts."""
    from .db_reader import WeChatDB
    db = WeChatDB(key_hex=args.key, data_dir=args.data_dir)
    name2id = db.get_name2id()
    print(f"共 {len(name2id)} 个对话:\n")
    for table, username in sorted(name2id.items(), key=lambda x: x[1]):
        print(f"  {username:40s}  ({table})")


def cmd_analyze(args):
    """Analyze chat messages."""
    from .db_reader import WeChatDB
    from .analyzer import analyze_chats, format_report
    db = WeChatDB(key_hex=args.key, data_dir=args.data_dir)
    analysis = analyze_chats(db, days=args.days)
    if args.json:
        print(json.dumps(analysis, ensure_ascii=False, indent=2))
    else:
        print(format_report(analysis))


def cmd_export(args):
    """Export messages."""
    from .db_reader import WeChatDB
    db = WeChatDB(key_hex=args.key, data_dir=args.data_dir)
    db.export_messages(args.output, days=args.days, fmt=args.format)


def _add_key_args(parser):
    """Add common key and data-dir arguments."""
    parser.add_argument("--key", required=True, help="64 位十六进制密钥")
    parser.add_argument("--data-dir", help="WeChat db_storage 目录 (自动检测)")


def main():
    parser = argparse.ArgumentParser(
        prog="wechat-decrypt",
        description="WeChat macOS 聊天记录解密与分析工具",
    )
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", help="子命令")

    # check
    p = sub.add_parser("check", help="检查前置条件")
    p.set_defaults(func=cmd_check)

    # resign
    p = sub.add_parser("resign", help="重签名 WeChat (需要 sudo)")
    p.add_argument("--guide", action="store_true", help="显示重签名指南")
    p.add_argument("--app-path", default="/Applications/WeChat.app", help="WeChat.app 路径")
    p.set_defaults(func=cmd_resign)

    # extract-key
    p = sub.add_parser("extract-key", help="从 WeChat 进程内存提取密钥 (需要 sudo)")
    p.add_argument("--pid", type=int, help="WeChat 进程 PID (自动检测)")
    p.add_argument("--db", help="数据库文件路径 (自动检测)")
    p.add_argument("--save", metavar="FILE", help="保存密钥到文件")
    p.set_defaults(func=cmd_extract_key)

    # query
    p = sub.add_parser("query", help="执行 SQL 查询")
    _add_key_args(p)
    p.add_argument("db_path", help="数据库文件路径")
    p.add_argument("sql", help="SQL 语句")
    p.add_argument("--json", action="store_true", help="输出 JSON 格式")
    p.set_defaults(func=cmd_query)

    # list-chats
    p = sub.add_parser("list-chats", help="列出所有聊天对话")
    _add_key_args(p)
    p.set_defaults(func=cmd_list_chats)

    # analyze
    p = sub.add_parser("analyze", help="分析聊天内容")
    _add_key_args(p)
    p.add_argument("--days", type=int, default=30, help="分析最近 N 天 (默认 30)")
    p.add_argument("--json", action="store_true", help="输出 JSON 格式")
    p.set_defaults(func=cmd_analyze)

    # export
    p = sub.add_parser("export", help="导出聊天记录")
    _add_key_args(p)
    p.add_argument("output", help="输出文件路径")
    p.add_argument("--days", type=int, default=30, help="导出最近 N 天 (默认 30)")
    p.add_argument("--format", choices=["json", "csv"], default="json", help="输出格式 (默认 json)")
    p.set_defaults(func=cmd_export)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)
    args.func(args)


if __name__ == "__main__":
    main()
