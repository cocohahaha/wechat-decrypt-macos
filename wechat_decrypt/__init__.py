"""Decrypt and analyze WeChat chat databases on macOS."""

__version__ = "0.1.0"

from .key_extractor import extract_key
from .db_reader import WeChatDB
from .analyzer import analyze_chats, format_report
