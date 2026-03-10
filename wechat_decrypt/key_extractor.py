"""Extract SQLCipher encryption key from WeChat process memory via Mach VM APIs."""

import ctypes
import ctypes.util
import os
import struct
import subprocess
import sys

from .utils import entropy, find_sqlcipher, find_wechat_data_dir, get_db_salt, get_wechat_pid


# ---------- Mach VM API bindings ----------

_libc = ctypes.CDLL(ctypes.util.find_library("c"))

_mach_task_self = _libc.mach_task_self
_mach_task_self.restype = ctypes.c_uint

_task_for_pid = _libc.task_for_pid
_task_for_pid.argtypes = [
    ctypes.c_uint, ctypes.c_int, ctypes.POINTER(ctypes.c_uint),
]
_task_for_pid.restype = ctypes.c_int

_mach_vm_read_overwrite = _libc.mach_vm_read_overwrite
_mach_vm_read_overwrite.argtypes = [
    ctypes.c_uint, ctypes.c_uint64, ctypes.c_uint64,
    ctypes.c_uint64, ctypes.POINTER(ctypes.c_uint64),
]
_mach_vm_read_overwrite.restype = ctypes.c_int

_mach_vm_region = _libc.mach_vm_region
_mach_vm_region.argtypes = [
    ctypes.c_uint,
    ctypes.POINTER(ctypes.c_uint64),
    ctypes.POINTER(ctypes.c_uint64),
    ctypes.c_int,
    ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_uint),
    ctypes.POINTER(ctypes.c_uint),
]
_mach_vm_region.restype = ctypes.c_int


class MachProcess:
    """Read memory from a macOS process using Mach VM APIs."""

    def __init__(self, pid):
        self.pid = pid
        self._task = ctypes.c_uint()
        ret = _task_for_pid(
            _mach_task_self(), pid, ctypes.byref(self._task)
        )
        if ret != 0:
            raise PermissionError(
                f"task_for_pid 失败 (返回值={ret})。\n"
                "可能的原因:\n"
                "  1. 未使用 sudo 运行\n"
                "  2. WeChat 未经重签名 (运行 sudo wechat-decrypt resign)\n"
                "  3. PID 不正确"
            )

    @property
    def task(self):
        return self._task.value

    def read_memory(self, address, size):
        """Read process memory. Returns bytes or None on failure."""
        buf = ctypes.create_string_buffer(size)
        outsize = ctypes.c_uint64(0)
        ret = _mach_vm_read_overwrite(
            self.task, address, size,
            ctypes.cast(buf, ctypes.c_void_p).value,
            ctypes.byref(outsize),
        )
        if ret != 0:
            return None
        return buf.raw[: outsize.value]

    def get_rw_regions(self):
        """Enumerate readable+writable memory regions."""
        regions = []
        address = ctypes.c_uint64(0)
        size = ctypes.c_uint64(0)
        info = (ctypes.c_uint * 12)()
        info_count = ctypes.c_uint(12)
        obj = ctypes.c_uint(0)

        while True:
            info_count.value = 12
            ret = _mach_vm_region(
                self.task,
                ctypes.byref(address),
                ctypes.byref(size),
                9,  # VM_REGION_BASIC_INFO_64
                ctypes.cast(info, ctypes.c_void_p),
                ctypes.byref(info_count),
                ctypes.byref(obj),
            )
            if ret != 0:
                break
            # info[0] = protection; READ=1, WRITE=2
            if info[0] & 3 == 3:
                regions.append((address.value, size.value))
            address.value += size.value

        return regions


def _find_salt_in_memory(proc, regions, salt):
    """Find all occurrences of the salt bytes in RW memory."""
    addrs = []
    for base, size in regions:
        chunk_size = min(size, 16 * 1024 * 1024)
        for off in range(0, size, chunk_size):
            read_size = min(chunk_size, size - off)
            data = proc.read_memory(base + off, read_size)
            if not data:
                continue
            pos = 0
            while True:
                idx = data.find(salt, pos)
                if idx == -1:
                    break
                addrs.append(base + off + idx)
                pos = idx + 1
    return addrs


def _is_plausible_key(data):
    """Check if 32 bytes could be a key (high entropy, not ASCII text)."""
    if not data or len(data) < 32:
        return False
    if entropy(data) < 3.5:
        return False
    ascii_count = sum(1 for b in data if 0x20 <= b <= 0x7E)
    if ascii_count > 24:
        return False
    return True


def verify_key(key_hex, db_path):
    """Verify a candidate key against a SQLCipher database.

    Returns (success, info_string).
    """
    sqlcipher = find_sqlcipher()
    configs = [
        (4, 4096),
        (4, 1024),
        (3, 4096),
        (3, 1024),
    ]
    for compat, page_size in configs:
        cmd = (
            f"PRAGMA key = \"x'{key_hex}'\";\n"
            f"PRAGMA cipher_compatibility = {compat};\n"
            f"PRAGMA cipher_page_size = {page_size};\n"
            "SELECT 'FOUND_TABLE:' || name FROM sqlite_master "
            "WHERE type='table' LIMIT 3;\n"
        )
        try:
            result = subprocess.run(
                [sqlcipher, db_path],
                input=cmd, capture_output=True, text=True, timeout=5,
            )
            tables = [
                line.strip().replace("FOUND_TABLE:", "")
                for line in result.stdout.strip().split("\n")
                if line.strip().startswith("FOUND_TABLE:")
            ]
            if tables:
                return True, f"compat={compat},page={page_size},tables={tables}"
        except Exception:
            pass
    return False, ""


def extract_key(pid=None, db_path=None, verbose=True):
    """Extract the SQLCipher key from a running WeChat process.

    Args:
        pid: WeChat process ID (auto-detected if None)
        db_path: Path to a message .db file (auto-detected if None)
        verbose: Print progress messages

    Returns:
        The 64-char hex key string.

    Raises:
        RuntimeError: If the key cannot be found.
        PermissionError: If task_for_pid fails.
    """
    if pid is None:
        pid = get_wechat_pid()
    if db_path is None:
        data_dir = find_wechat_data_dir()
        db_path = os.path.join(data_dir, "message", "message_0.db")

    if not os.path.exists(db_path):
        raise FileNotFoundError(f"数据库不存在: {db_path}")

    salt = get_db_salt(db_path)
    if verbose:
        print(f"PID: {pid}")
        print(f"数据库: {os.path.basename(db_path)}")
        print(f"Salt: {salt.hex()}")

    proc = MachProcess(pid)
    regions = proc.get_rw_regions()
    if verbose:
        print(f"RW 内存区域: {len(regions)}")

    # Step 1: Find salt locations in memory
    salt_addrs = _find_salt_in_memory(proc, regions, salt)
    if verbose:
        print(f"Salt 在内存中出现 {len(salt_addrs)} 次")

    # Step 2: Search for pointers TO the salt → find codec_ctx → cipher_ctx → key
    if verbose:
        print("搜索 codec_ctx 结构体...")

    candidates = []

    for salt_addr in salt_addrs:
        ptr_bytes = struct.pack("<Q", salt_addr)
        for base, size in regions:
            if size > 200 * 1024 * 1024:
                continue
            chunk_size = min(size, 16 * 1024 * 1024)
            for off in range(0, size, chunk_size):
                read_size = min(chunk_size, size - off)
                data = proc.read_memory(base + off, read_size)
                if not data:
                    continue
                pos = 0
                while True:
                    idx = data.find(ptr_bytes, pos)
                    if idx == -1:
                        break
                    ptr_loc = base + off + idx

                    # Read the structure around this pointer
                    ctx = proc.read_memory(ptr_loc - 64, 256)
                    if not ctx:
                        pos = idx + 1
                        continue

                    # Follow all pointer-like values in the structure
                    for p_off in range(0, 256, 8):
                        val = struct.unpack("<Q", ctx[p_off : p_off + 8])[0]
                        if not (0x100000000 < val < 0x800000000000):
                            continue
                        # Read what the pointer points to
                        pointed = proc.read_memory(val, 64)
                        if not pointed:
                            continue

                        # Check 32-byte chunks at offsets 0 and 32
                        for key_off in (0, 32):
                            chunk = pointed[key_off : key_off + 32]
                            if _is_plausible_key(chunk):
                                candidates.append(chunk.hex())

                        # Follow one more level of indirection
                        for p2_off in range(0, min(64, len(pointed)), 8):
                            p2 = struct.unpack(
                                "<Q", pointed[p2_off : p2_off + 8]
                            )[0]
                            if not (0x100000000 < p2 < 0x800000000000):
                                continue
                            deep = proc.read_memory(p2, 64)
                            if not deep:
                                continue
                            for key_off in (0, 32):
                                chunk = deep[key_off : key_off + 32]
                                if _is_plausible_key(chunk):
                                    candidates.append(chunk.hex())

                    pos = idx + 1

    # Step 3: Also try direct offsets near the salt
    for salt_addr in salt_addrs:
        for offset in (-128, -96, -64, -32, 32, 64):
            chunk = proc.read_memory(salt_addr + offset, 32)
            if chunk and _is_plausible_key(chunk):
                candidates.append(chunk.hex())

    # Deduplicate
    candidates = list(dict.fromkeys(candidates))
    if verbose:
        print(f"候选密钥: {len(candidates)} 个")

    # Step 4: Verify candidates
    if verbose:
        print("验证候选密钥...")

    for i, key_hex in enumerate(candidates):
        ok, info = verify_key(key_hex, db_path)
        if ok:
            if verbose:
                print(f"\n密钥已找到！")
                print(f"  Key: {key_hex}")
                print(f"  Info: {info}")
            return key_hex

    # Step 5: Fallback — brute-force around salt with wider range
    if verbose:
        print("扩大搜索范围...")
    for salt_addr in salt_addrs:
        for offset in range(-8192, 8192, 8):
            chunk = proc.read_memory(salt_addr + offset, 32)
            if not chunk or not _is_plausible_key(chunk):
                continue
            key_hex = chunk.hex()
            ok, info = verify_key(key_hex, db_path)
            if ok:
                if verbose:
                    print(f"\n密钥已找到！")
                    print(f"  Key: {key_hex}")
                    print(f"  Info: {info}")
                return key_hex

    raise RuntimeError(
        "未能找到密钥。可能的原因:\n"
        "  1. WeChat 未完全启动或未登录\n"
        "  2. WeChat 版本不兼容\n"
        "  3. 数据库路径不正确"
    )
