"""
I-9 阶段一：只读内存定位 gbbq 4176B 密码表。

纪律（硬约束）：
- 仅 OpenProcess(PROCESS_VM_READ|PROCESS_QUERY_INFORMATION) + VirtualQueryEx + ReadProcessMemory
- 严禁 WriteProcessMemory / 注入 / 挂钩 / 结束进程 / 改任何 D:\\tdx 文件
- dump 出的密钥只落盘 data/cache/tdx_gbbq/_key.bin（chmod 0600）
- 报告/日志只登记 SHA256 + 前 4 字节掩码，绝不打印密钥明文

定位方法：用公开方案（MIT 许可 rustdx，交叉引用 pytdx/CSDN/新浪博客）登记的
4176B 固定密码表前 16 字节作签名，在进程可读 committed 区里搜索；命中后只读
取完整 4176B，逐个 candidate 用解密自检（代码 ASCII/日期区间/category 范围）验证。
"""
import ctypes
import hashlib
import os
import sys
from ctypes import wintypes

PID = int(sys.argv[1]) if len(sys.argv) > 1 else 14192
OUT = sys.argv[2] if len(sys.argv) > 2 else "data/cache/tdx_gbbq/_key.bin"

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.OpenProcess.restype = wintypes.HANDLE
k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
# PROCESS_VM_READ=0x0010 | PROCESS_QUERY_INFORMATION=0x0400
h = k32.OpenProcess(0x0410, False, PID)
if not h:
    print("OPEN_FAILED err=", ctypes.get_last_error())
    sys.exit(2)

iswow = wintypes.BOOL()
k32.IsWow64Process.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
k32.IsWow64Process(h, ctypes.byref(iswow))
print("PID=", PID, "IsWow64=", iswow.value)


class MBI(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
    ]


k32.VirtualQueryEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.POINTER(MBI), ctypes.c_size_t]
k32.VirtualQueryEx.restype = ctypes.c_size_t
k32.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
k32.ReadProcessMemory.restype = wintypes.BOOL

MEM_COMMIT = 0x1000
MEM_MAPPED = 0x40000
MEM_IMAGE = 0x1000000
VALID_STATES = {MEM_COMMIT, MEM_MAPPED, MEM_IMAGE}
READABLE = {0x02, 0x04, 0x20, 0x40}  # RWX combo readonly/readwrite/exec_read/exec_readwrite
# 公开 4176B 固定密码表的前 16 字节签名（rustdx KEY[0:16]）
SIG = bytes([56, 167, 194, 29, 224, 106, 23, 226, 209, 57, 162, 64, 156, 186, 70, 175])
KEYSIZE = 4176
CHUNK = 1024 * 1024
OVERLAP = KEYSIZE

addr = 0
mbi = MBI()
regions = 0
bytes_read = 0
hits = {}  # abs_addr -> sha
max_addr = 0x7FFFFFFFFFFF

while addr < max_addr:
    r = k32.VirtualQueryEx(h, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi))
    if r == 0:
        break
    base = mbi.BaseAddress or 0
    size = mbi.RegionSize or 0
    if size == 0:
        break
    if (mbi.State in VALID_STATES) and ((mbi.Protect & 0x1F) in READABLE) and not (mbi.Protect & 0x100) and base:
        regions += 1
        off = 0
        prev_tail = b""
        while off < size:
            toread = min(CHUNK, size - off)
            buf = ctypes.create_string_buffer(toread)
            nr = ctypes.c_size_t(0)
            ok = k32.ReadProcessMemory(h, ctypes.c_void_p(base + off), buf, toread, ctypes.byref(nr))
            if ok and nr.value > 0:
                cur = buf.raw[: nr.value]
                bytes_read += len(cur)
                data = prev_tail + cur
                chunk_abs = base + off - len(prev_tail)
                start = 0
                while True:
                    idx = data.find(SIG, start)
                    if idx < 0:
                        break
                    abs_addr = chunk_abs + idx
                    keybuf = ctypes.create_string_buffer(KEYSIZE)
                    nk = ctypes.c_size_t(0)
                    ok2 = k32.ReadProcessMemory(h, ctypes.c_void_p(abs_addr), keybuf, KEYSIZE, ctypes.byref(nk))
                    if ok2 and nk.value == KEYSIZE:
                        kb = keybuf.raw
                        hits.setdefault(abs_addr, hashlib.sha256(kb).hexdigest())
                    start = idx + 1
                prev_tail = cur[-OVERLAP:]
            else:
                prev_tail = b""
            off += toread
    nxt = base + size
    if nxt <= addr:
        break
    addr = nxt

k32.CloseHandle(h)
print("regions_scanned=", regions, "bytes_read=", bytes_read, "sig_hits=", len(hits))
for a, sha in sorted(hits.items()):
    print("HIT addr=0x%x sha256=%s" % (a, sha))

if not hits:
    print("NO_KEY_FOUND")
    sys.exit(3)

# 取第一个命中写入 _key.bin（仅本批使用；后续解密自检会挑出真正可用的）
first_addr = sorted(hits.keys())[0]
keybuf = ctypes.create_string_buffer(KEYSIZE)
h2 = k32.OpenProcess(0x0410, False, PID)
nk = ctypes.c_size_t(0)
ok = k32.ReadProcessMemory(h2, ctypes.c_void_p(first_addr), keybuf, KEYSIZE, ctypes.byref(nk))
k32.CloseHandle(h2)
if not ok or nk.value != KEYSIZE:
    print("REREAD_FAILED")
    sys.exit(4)
kb = keybuf.raw
os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "wb") as f:
    f.write(kb)
try:
    os.chmod(OUT, 0o600)
except Exception:
    pass
sha = hashlib.sha256(kb).hexdigest()
masked = kb[0:1].hex() + "??" + "??" + kb[3:4].hex()
print("DUMPED addr=0x%x bytes=%d sha256=%s first4_masked=%s" % (first_addr, len(kb), sha, masked))
print("OUT=", OUT)
