"""Re-sign WeChat to allow memory access for key extraction."""

import os
import plistlib
import shutil
import subprocess
import sys
import tempfile


WECHAT_APP = "/Applications/WeChat.app"

# Full entitlements needed for WeChat to function properly
ENTITLEMENTS = {
    "com.apple.security.get-task-allow": True,
    "com.apple.security.app-sandbox": True,
    "com.apple.security.cs.allow-jit": True,
    "com.apple.security.cs.disable-library-validation": True,
    "com.apple.security.device.audio-input": True,
    "com.apple.security.device.camera": True,
    "com.apple.security.device.usb": True,
    "com.apple.security.files.bookmarks.app-scope": True,
    "com.apple.security.files.downloads.read-write": True,
    "com.apple.security.files.user-selected.read-write": True,
    "com.apple.security.network.client": True,
    "com.apple.security.network.server": True,
    "com.apple.security.personal-information.location": True,
    "com.apple.security.personal-information.photos-library": True,
    "com.apple.security.print": True,
}


def check_codesign_status(app_path=WECHAT_APP):
    """Check current code signing status of WeChat."""
    binary = os.path.join(app_path, "Contents/MacOS/WeChat")
    if not os.path.exists(binary):
        raise FileNotFoundError(f"WeChat 未安装: {app_path}")

    result = subprocess.run(
        ["codesign", "-dvv", binary],
        capture_output=True, text=True,
    )
    output = result.stderr  # codesign outputs to stderr

    info = {
        "has_hardened_runtime": "flags=0x10000(runtime)" in output,
        "is_adhoc": "flags=0x2(adhoc)" in output,
    }

    # Check for get-task-allow entitlement
    result2 = subprocess.run(
        ["codesign", "-d", "--entitlements", "-", "--xml", binary],
        capture_output=True,
    )
    try:
        plist = plistlib.loads(result2.stdout)
        info["has_get_task_allow"] = plist.get(
            "com.apple.security.get-task-allow", False
        )
    except Exception:
        info["has_get_task_allow"] = False

    return info


def _extract_app_identifier(app_path=WECHAT_APP):
    """Extract the application identifier from existing entitlements."""
    binary = os.path.join(app_path, "Contents/MacOS/WeChat")
    result = subprocess.run(
        ["codesign", "-d", "--entitlements", "-", "--xml", binary],
        capture_output=True,
    )
    try:
        plist = plistlib.loads(result.stdout)
        return plist.get("com.apple.application-identifier")
    except Exception:
        return None


def resign_wechat(app_path=WECHAT_APP):
    """Re-sign WeChat with get-task-allow entitlement.

    This removes the hardened runtime flag, allowing task_for_pid
    to read the process memory for key extraction.

    Must be run as root (sudo).
    """
    if os.geteuid() != 0:
        raise PermissionError("需要 root 权限，请使用 sudo 运行")

    binary = os.path.join(app_path, "Contents/MacOS/WeChat")
    if not os.path.exists(binary):
        raise FileNotFoundError(f"WeChat 未安装: {app_path}")

    # Build entitlements plist
    ent = dict(ENTITLEMENTS)
    app_id = _extract_app_identifier(app_path)
    if app_id:
        ent["com.apple.application-identifier"] = app_id
        ent["com.apple.security.application-groups"] = [app_id]

    # Write entitlements to temp file
    with tempfile.NamedTemporaryFile(suffix=".plist", delete=False) as f:
        plistlib.dump(ent, f)
        ent_path = f.name

    try:
        print(f"正在重签名 {app_path} ...")
        result = subprocess.run(
            [
                "codesign", "--force", "--deep", "--sign", "-",
                "--entitlements", ent_path,
                app_path,
            ],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"重签名失败: {result.stderr}")
        print("重签名成功！请重新启动 WeChat。")
    finally:
        os.unlink(ent_path)


def print_resign_guide():
    """Print step-by-step guide for re-signing WeChat."""
    print("""
=== WeChat 重签名指南 ===

为什么需要重签名？
  WeChat 使用了 macOS 的 Hardened Runtime 保护，阻止了内存读取。
  重签名会移除此保护，添加 get-task-allow 权限，使密钥提取成为可能。

注意事项：
  1. 重签名会修改 WeChat.app，WeChat 更新后需重新执行
  2. 重签名后的 WeChat 安全性略有降低
  3. 仅影响本地安装的 WeChat

步骤：
  1. 退出 WeChat
  2. 执行: sudo wechat-decrypt resign
  3. 重新启动 WeChat 并登录
  4. 执行: sudo wechat-decrypt extract-key --save key.txt
  5. 密钥提取完成后可正常使用 WeChat
""")
