"""Shared utilities."""

import glob
import math
import os
import shutil
import subprocess


def find_wechat_data_dir():
    """Auto-discover WeChat db_storage directory."""
    pattern = os.path.expanduser(
        "~/Library/Containers/com.tencent.xinWeChat/Data/Documents/"
        "xwechat_files/*/db_storage/message/message_0.db"
    )
    matches = glob.glob(pattern)
    if not matches:
        raise FileNotFoundError(
            "未找到 WeChat 数据目录。请确认 WeChat 已登录并产生过消息。\n"
            f"搜索路径: {pattern}"
        )
    # Pick the most recently modified
    best = max(matches, key=os.path.getmtime)
    # Return the db_storage directory
    return os.path.dirname(os.path.dirname(best))


def find_sqlcipher():
    """Find sqlcipher binary."""
    candidates = [
        "/opt/homebrew/bin/sqlcipher",
        "/usr/local/bin/sqlcipher",
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    found = shutil.which("sqlcipher")
    if found:
        return found
    raise FileNotFoundError(
        "未找到 sqlcipher，请先安装: brew install sqlcipher"
    )


def get_wechat_pid():
    """Get WeChat process PID."""
    result = subprocess.run(
        ["pgrep", "-x", "WeChat"],
        capture_output=True, text=True,
    )
    pids = result.stdout.strip().split()
    if not pids:
        raise RuntimeError("WeChat 未运行。请先启动 WeChat。")
    return int(pids[0])


def get_db_salt(db_path):
    """Read the first 16 bytes (salt) from a SQLCipher database file."""
    with open(db_path, "rb") as f:
        salt = f.read(16)
    if len(salt) < 16:
        raise ValueError(f"数据库文件过小: {db_path}")
    return salt


def entropy(data):
    """Calculate Shannon entropy of bytes."""
    if not data:
        return 0.0
    freq = {}
    for b in data:
        freq[b] = freq.get(b, 0) + 1
    ent = 0.0
    for count in freq.values():
        p = count / len(data)
        ent -= p * math.log2(p)
    return ent


def check_prerequisites():
    """Check that all prerequisites are met. Returns list of issues."""
    import platform
    issues = []
    if platform.system() != "Darwin":
        issues.append("此工具仅支持 macOS")
    try:
        find_sqlcipher()
    except FileNotFoundError as e:
        issues.append(str(e))
    try:
        find_wechat_data_dir()
    except FileNotFoundError as e:
        issues.append(str(e))
    return issues
