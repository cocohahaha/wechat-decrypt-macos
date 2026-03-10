"""Read and query encrypted WeChat SQLCipher databases."""

import csv
import glob
import hashlib
import io
import os
import subprocess

from .utils import find_sqlcipher, find_wechat_data_dir


class WeChatDB:
    """Interface to encrypted WeChat databases."""

    def __init__(self, key_hex, data_dir=None, sqlcipher_path=None):
        self.key_hex = key_hex
        self.data_dir = data_dir or find_wechat_data_dir()
        self.sqlcipher = sqlcipher_path or find_sqlcipher()

    def _preamble(self):
        return (
            f"PRAGMA key = \"x'{self.key_hex}'\";\n"
            "PRAGMA cipher_compatibility = 4;\n"
            "PRAGMA cipher_page_size = 4096;\n"
        )

    def query_raw(self, db_path, sql):
        """Execute SQL and return raw output lines (excluding 'ok')."""
        cmd = self._preamble() + sql
        result = subprocess.run(
            [self.sqlcipher, db_path],
            input=cmd.encode(),
            capture_output=True,
            timeout=30,
        )
        text = result.stdout.decode("utf-8", errors="replace").strip()
        return [l for l in text.split("\n") if l.strip() and l.strip() != "ok"]

    def query(self, db_path, sql):
        """Execute SQL and return list of dicts (CSV-parsed)."""
        cmd = self._preamble() + ".headers on\n.mode csv\n" + sql
        result = subprocess.run(
            [self.sqlcipher, db_path],
            input=cmd.encode(),
            capture_output=True,
            timeout=30,
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

    def list_tables(self, db_path):
        """List all table names in a database."""
        return self.query_raw(
            db_path,
            "SELECT name FROM sqlite_master WHERE type='table';",
        )

    def get_message_dbs(self):
        """Get paths to all message database files."""
        pattern = os.path.join(self.data_dir, "message", "message_[0-9].db")
        return sorted(glob.glob(pattern))

    def get_msg_tables(self, db_path):
        """Get all Msg_* table names from a database."""
        tables = self.list_tables(db_path)
        return [t.strip() for t in tables if t.strip().startswith("Msg_")]

    def get_name2id(self, db_path=None):
        """Build a mapping from Msg_ table name to username (wxid).

        Returns: {table_name: username, ...}
        """
        if db_path is None:
            dbs = self.get_message_dbs()
            if not dbs:
                return {}
            db_path = dbs[0]

        rows = self.query(db_path, "SELECT user_name FROM Name2Id;")
        mapping = {}
        for row in rows:
            un = row.get("user_name", "")
            if un:
                h = hashlib.md5(un.encode()).hexdigest()
                mapping[f"Msg_{h}"] = un
        return mapping

    def get_messages(self, table, db_path, limit=100, since=0):
        """Get messages from a specific Msg_* table.

        Args:
            table: Table name (e.g. "Msg_abc123...")
            db_path: Path to the database file
            limit: Maximum number of messages
            since: Unix timestamp, only return messages after this time

        Returns: List of message dicts with keys:
            local_id, create_time, local_type, message_content, source
        """
        where = f"WHERE create_time > {since}" if since else ""
        return self.query(
            db_path,
            f"SELECT local_id, create_time, local_type, "
            f"real_sender_id, message_content, source "
            f"FROM {table} {where} "
            f"ORDER BY create_time DESC LIMIT {limit};",
        )

    def get_all_recent_messages(self, days=30, limit_per_table=100):
        """Collect recent messages from all message databases.

        Returns: List of message dicts, sorted by time (newest first).
        """
        import time
        since = int(time.time()) - days * 86400
        all_msgs = []

        for db_path in self.get_message_dbs():
            for table in self.get_msg_tables(db_path):
                rows = self.get_messages(
                    table, db_path, limit=limit_per_table, since=since
                )
                for r in rows:
                    r["_table"] = table
                    r["_db"] = os.path.basename(db_path)
                all_msgs.extend(rows)

        all_msgs.sort(
            key=lambda x: int(x.get("create_time", "0") or "0"),
            reverse=True,
        )
        return all_msgs

    def export_messages(self, output_path, days=30, fmt="json"):
        """Export decrypted messages to a file.

        Args:
            output_path: Output file path
            days: Number of days to export
            fmt: "json" or "csv"
        """
        import json

        msgs = self.get_all_recent_messages(days=days, limit_per_table=500)
        name2id = self.get_name2id()

        # Resolve table names to contact names
        for m in msgs:
            m["contact"] = name2id.get(m.get("_table", ""), m.get("_table", ""))

        if fmt == "json":
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(msgs, f, ensure_ascii=False, indent=2)
        elif fmt == "csv":
            if not msgs:
                return
            keys = ["create_time", "local_type", "contact", "message_content"]
            with open(output_path, "w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(msgs)

        print(f"已导出 {len(msgs)} 条消息到 {output_path}")
