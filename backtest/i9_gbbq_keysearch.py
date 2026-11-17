"""I-9 阶段一：只读扫描进程内存，自校验定位 4176B gbbq 密码表。

不依赖任何公开 key 字节（实机版本相关，公开表在本机内存/二进制均为 0 命中）。
方法：把可读内存按 4 种对齐切成 uint32 视图，对每个候选 4176B 窗口用公开方案的
Feistel 解密 gbbq 首条记录第 0 个 8B 分组；正确 key 会解出 market∈{0,1} 且
6 位代码为 ASCII 数字（误命概率 ~3e-11）。命中后只读回完整 4176B 写 _key.bin。

纪律：仅 OpenProcess(VM_READ|QUERY_INFO)+VirtualQueryEx+ReadProcessMemory；
严禁写入/注入/挂钩/结束进程；密钥明文绝不打印，只报 sha256 + 前4字节掩码。
"""
import ctypes, hashlib, os, sys
from ctypes import wintypes
import numpy as np

PID = int(sys.argv[1]) if len(sys.argv) > 1 else 14192
OUT = sys.argv[2] if len(sys.argv) > 2 else "data/cache/tdx_gbbq/_key.bin"
GBBQ = sys.argv[3] if len(sys.argv) > 3 else "D:/tdx/T0002/hq_cache/gbbq"
KEYLEN = 4176

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.OpenProcess.restype = wintypes.HANDLE
k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
h = k32.OpenProcess(0x0410, False, PID)
if not h:
    print("OPEN_FAILED", ctypes.get_last_error()); sys.exit(2)
class MBI(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
                ("AllocationProtect", wintypes.DWORD), ("RegionSize", ctypes.c_size_t),
                ("State", wintypes.DWORD), ("Protect", wintypes.DWORD), ("Type", wintypes.DWORD)]
k32.VirtualQueryEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.POINTER(MBI), ctypes.c_size_t]
k32.VirtualQueryEx.restype = ctypes.c_size_t
k32.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
k32.ReadProcessMemory.restype = wintypes.BOOL

VALID_STATES = {0x1000, 0x40000, 0x1000000}   # COMMIT / MAPPED / IMAGE
def readable(prot):
    return ((prot & 0x1F) in (0x02, 0x04, 0x20, 0x40)) and not (prot & 0x100)

# ---- gbbq 首条记录密文（前 8B）----
raw = open(GBBQ, "rb").read()
C0 = int.from_bytes(raw[4:8], "little")   # encrypt[0:4]
C1 = int.from_bytes(raw[8:12], "little")  # encrypt[4:8]

# ---- 纯 Python 参考解密（用于自检向量化实现）----
def u32(b, i):
    return int.from_bytes(b[i:i+4], "little")
def parse_block(key, c0, c1):
    num = u32(key, 0x44) ^ c0
    numold = c1
    M = 0xffffffff
    for j in range(64, 0, -4):
        eax = u32(key, ((num >> 16) & 0xff) * 4 + 0x448)
        eax = (eax + u32(key, ((num >> 24) & 0xff) * 4 + 0x48)) & M
        eax ^= u32(key, ((num >> 8) & 0xff) * 4 + 0x848)
        eax = (eax + u32(key, (num & 0xff) * 4 + 0xc48)) & M
        eax ^= u32(key, j)
        num, numold = numold ^ eax, num
    numold ^= u32(key, 0)
    return numold, num  # 明文 0:4, 4:8

def valid_plain(p0, p1):
    a = (p0 & 0xff, (p0 >> 8) & 0xff, (p0 >> 16) & 0xff, (p0 >> 24) & 0xff)
    b = (p1 & 0xff, (p1 >> 8) & 0xff, (p1 >> 16) & 0xff, (p1 >> 24) & 0xff)
    code = a[1:4] + b[0:3]
    return (a[0] in (0, 1)) and all(0x30 <= c <= 0x39 for c in code)

# ---- 向量化块解密：V=uint32 内存视图，w=候选窗口的“字”起始下标(数组) ----
T0, T1, T2, T3 = 0x48//4, 0x448//4, 0x848//4, 0xc48//4   # 18,274,530,786
INIT, FINAL = 0x44//4, 0                                  # 17,0
def parse_block_vec(V, w, c0, c1):
    M = np.uint32(0xffffffff)
    num = np.bitwise_xor(V[w + INIT], np.uint32(c0)).astype(np.uint32)
    numold = np.full(w.shape, np.uint32(c1), dtype=np.uint32)
    for jw in range(16, 0, -1):   # j = 64,60,...,4  -> jw=j/4
        idx1 = (num >> np.uint32(16)) & np.uint32(0xff)
        idx2 = (num >> np.uint32(24)) & np.uint32(0xff)
        idx3 = (num >> np.uint32(8))  & np.uint32(0xff)
        idx4 = num & np.uint32(0xff)
        eax = V[w + T1 + idx1].astype(np.uint32)
        eax = (eax + V[w + T0 + idx2].astype(np.uint32)) & M
        eax = eax ^ V[w + T2 + idx3].astype(np.uint32)
        eax = (eax + V[w + T3 + idx4].astype(np.uint32)) & M
        eax = eax ^ V[w + jw].astype(np.uint32)
        num, numold = (numold ^ eax).astype(np.uint32), num
    numold = numold ^ V[w + FINAL].astype(np.uint32)
    return numold, num

def valid_vec(p0, p1):
    a0 = (p0 & np.uint32(0xff))
    ok = (a0 == 0) | (a0 == 1)
    for sh in (8, 16, 24):
        c = (p0 >> np.uint32(sh)) & np.uint32(0xff)
        ok &= (c >= np.uint32(0x30)) & (c <= np.uint32(0x39))
    for sh in (0, 8, 16):
        c = (p1 >> np.uint32(sh)) & np.uint32(0xff)
        ok &= (c >= np.uint32(0x30)) & (c <= np.uint32(0x39))
    return ok

# ---- 自检：随机 key 上向量化实现必须与纯 Python 一致 ----
rng = np.random.default_rng(9)
for _ in range(50):
    kb = rng.integers(0, 256, KEYLEN, dtype=np.uint8).tobytes()
    Vt = np.frombuffer(kb, dtype=np.uint32)
    p0, p1 = parse_block(kb, C0, C1)
    q0, q1 = parse_block_vec(Vt, np.array([0], dtype=np.int64), C0, C1)
    assert int(q0[0]) == p0 and int(q1[0]) == p1, "vector port mismatch"
print("SELFTEST vector port OK (50 random keys)")

# ---- 扫描 ----
addr = 0; mbi = MBI(); regions = 0; scanned = 0
hits = []   # (abs_byte_addr, sha256)
CHUNK = 50000  # 每批候选窗口数（控内存）
while addr < 0x7FFFFFFFFFFF:
    r = k32.VirtualQueryEx(h, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi))
    if r == 0: break
    base = mbi.BaseAddress or 0; size = mbi.RegionSize or 0
    if size == 0: break
    if (mbi.State in VALID_STATES) and readable(mbi.Protect) and base and size >= KEYLEN:
        buf = ctypes.create_string_buffer(size); nr = ctypes.c_size_t(0)
        if k32.ReadProcessMemory(h, ctypes.c_void_p(base), buf, size, ctypes.byref(nr)) and nr.value == size:
            regions += 1; scanned += size
            if regions % 50 == 1:
                print("scan region %d scanned=%.1fMB hits=%d" % (regions, scanned/1048576, len(hits)), flush=True)
            mem = np.frombuffer(buf.raw[:nr.value], dtype=np.uint8)
            for align in range(4):
                nwords = (len(mem) - align) // 4
                if nwords < KEYLEN//4 + 1: continue
                V = mem[align: align + nwords*4].view(np.uint32)
                wmax = nwords - (KEYLEN//4)   # 需要覆盖 KEY[4168..4171] => w+1042
                start = 0
                while start < wmax:
                    w = np.arange(start, min(start+CHUNK, wmax), dtype=np.int64)
                    p0, p1 = parse_block_vec(V, w, C0, C1)
                    ok = valid_vec(p0, p1)
                    for wi in w[ok]:
                        abs_addr = base + align + int(wi)*4
                        keybytes = V[int(wi):int(wi)+KEYLEN//4].view(np.uint8).tobytes()
                        if len(keybytes) == KEYLEN:
                            hits.append((abs_addr, hashlib.sha256(keybytes).hexdigest(), keybytes))
                    start += CHUNK
    nxt = base + size
    if nxt <= addr: break
    addr = nxt
k32.CloseHandle(h)
print("regions=", regions, "scanned_bytes=", scanned, "hits=", len(hits))

if not hits:
    print("NO_KEY_FOUND")
    sys.exit(3)

# 用首条记录全部 3 个分组 + 第二条记录做交叉确认
def full_ok(key):
    rec0 = raw[4:4+29]
    for g in range(3):
        c0 = int.from_bytes(rec0[g*8:g*8+4], "little")
        c1 = int.from_bytes(rec0[g*8+4:g*8+8], "little")
        parse_block(key, c0, c1)  # 不抛异常即可
    p0, p1 = parse_block(key, int.from_bytes(raw[4:8],"little"), int.from_bytes(raw[8:12],"little"))
    return valid_plain(p0, p1)

confirmed = [(a, s, kb) for (a, s, kb) in hits if full_ok(kb)]
print("confirmed_hits=", len(confirmed))
if not confirmed:
    print("HITS_BUT_NOT_CONFIRMED")
    sys.exit(4)

a, sha, kb = confirmed[0]
os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "wb") as f: f.write(kb)
try: os.chmod(OUT, 0o600)
except Exception: pass
masked = kb[0:1].hex() + "??????" + kb[3:4].hex()
print("FOUND addr=0x%x bytes=%d sha256=%s first4_masked=%s" % (a, len(kb), sha, masked))
print("OUT=", OUT)
