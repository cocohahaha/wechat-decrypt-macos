#!/bin/bash
# 一键安装 wechat-decrypt-macos MCP Server
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== 安装 wechat-decrypt-macos MCP Server ==="
echo ""

# 检查 Python 版本
if ! command -v python3 &>/dev/null; then
    echo "错误: 未找到 python3，请先安装 Python 3.10+"
    exit 1
fi

PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=$(python3 -c 'import sys; print(sys.version_info.major)')
PY_MINOR=$(python3 -c 'import sys; print(sys.version_info.minor)')

if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; }; then
    echo "错误: 需要 Python 3.10+，当前版本 $PY_VERSION"
    exit 1
fi
echo "Python $PY_VERSION"

# 检查 sqlcipher
if ! command -v sqlcipher &>/dev/null; then
    echo "未找到 sqlcipher，正在通过 Homebrew 安装..."
    if ! command -v brew &>/dev/null; then
        echo "错误: 未找到 Homebrew，请先安装: https://brew.sh"
        exit 1
    fi
    brew install sqlcipher
fi
echo "sqlcipher: $(which sqlcipher)"

# 创建虚拟环境
echo ""
echo "创建虚拟环境..."
python3 -m venv "$SCRIPT_DIR/.venv"
source "$SCRIPT_DIR/.venv/bin/activate"

# 安装项目
echo "安装依赖..."
pip install -e "$SCRIPT_DIR" --quiet

# 注册 MCP Server 到 Claude Code
echo ""
if command -v claude &>/dev/null; then
    echo "注册 MCP Server 到 Claude Code..."
    claude mcp add -s user wechat "$SCRIPT_DIR/.venv/bin/python" "$SCRIPT_DIR/server.py"
    echo "已注册到 Claude Code (user scope)"
else
    echo "Claude Code 未安装，跳过 MCP 注册。"
    echo "安装后可手动注册:"
    echo "  claude mcp add -s user wechat $SCRIPT_DIR/.venv/bin/python $SCRIPT_DIR/server.py"
fi

echo ""
echo "=== 安装完成 ==="
echo ""
echo "接下来:"
echo "  1. 重签名微信 (首次使用/微信更新后)"
echo "  2. 启动微信并登录"
echo "  3. 提取密钥保存到 key.txt"
echo "  4. 重启 Claude Code 即可使用微信 MCP 工具"
