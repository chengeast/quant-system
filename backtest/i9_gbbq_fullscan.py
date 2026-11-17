"""I-9 全量内存诊断（只读，不改任何数据）：
1) VirtualQueryEx 遍历全部可读区（含 IMAGE/MAPPED/COMMIT），统计字节数与区数；
2) 公开 rustdx 4176B key 前缀（4/8/16B）签名命中统计（区分 IMAGE 与 HEAP）；
3) 磁盘 gbbq 首条记录前 12B 密文签名在内存中的命中（gbbq 解密模块是否已加载）；
4) 对 IMAGE 区做 Feistel 自校验 4176B 窗口扫描（密码表若随 tdxw 模块加载则在其区内）。
不打印密钥明文，只登记 sha256。
"""
import ctypes, hashlib, os, sys, time
from ctypes import wintypes
import numpy as np

PID = int(sys.argv[1]) if len(sys.argv) > 1 else 14192
OUTLOG = sys.argv[2] if len(sys.argv) > 2 else "backtest/output/i9_fullscan.log"
GBBQ = sys.argv[3] if len(sys.argv) > 3 else "D:/tdx/T0002/hq_cache/gbbq"

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

MEM_COMMIT, MEM_MAPPED, MEM_IMAGE = 0x1000, 0x40000, 0x1000000
def readable(prot):
    if prot & 0x01: return False
    if prot & 0x100: return False
    return (prot & 0x1F) in (0x02, 0x04, 0x20, 0x40)

KEY16 = bytes([56,167,194,29,224,106,23,226,209,57,162,64,156,186,70,175])
raw = open(GBBQ, "rb").read()
GBBQ_REC = raw[4:16]   # 首条记录前 12B 密文（含偏移 12..16 的 06 86 6a 39）
print("GBBQ_REC_SIG=", GBBQ_REC.hex())

def region_type_str(st, tp):
    s = []
    if st == MEM_COMMIT: s.append("COMMIT")
    if st == MEM_MAPPED: s.append("MAPPED")
    if st == MEM_IMAGE: s.append("IMAGE")
    t = []
    if tp & 0x1000000: t.append("IMG")
    if tp & 0x40000: t.append("MAP")
    if tp & 0x20000: t.append("PRIVATE")
    return "+".join(s), "+".join(t) or "?"

# 第一遍：枚举全部可读区
addr = 0; mbi = MBI()
regions = []
while addr < 0x7FFFFFFFFFFF:
    r = k32.VirtualQueryEx(h, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi))
    if r == 0: break
    base = mbi.BaseAddress or 0; size = mbi.RegionSize or 0
    if size == 0: break
    if readable(mbi.Protect) and base:
        regions.append((base, size, mbi.State, mbi.Protect, mbi.Type))
    nxt = base + size
    if nxt <= addr: break
    addr = nxt
k32.CloseHandle(h)

total = sum(s for _, s, _, _, _ in regions)
img = sum(s for _, s, st, p, tp in regions if st == MEM_IMAGE or tp & 0x1000000)
print("TOTAL readable regions=%d bytes=%.1fMB (IMAGE/MAPPED bytes=%.1fMB)" % (len(regions), total/1048576, img/1048576))

# 第二遍：逐区读 + 签名统计 + IMAGE 区 Feistel 扫描
h = k32.OpenProcess(0x0410, False, PID)
C0 = int.from_bytes(raw[4:8], "little")
C1 = int.from_bytes(raw[8:12], "little")

# ---- Feistel 自校验（与 i9_gbbq_keysearch.py 同一实现）----
M = 0xffffffff
def u32(b, i): return int.from_bytes(b[i:i+4], "little")
T0, T1, T2, T3 = 18, 274, 530, 786
def parse_block_vec(V, w, c0, c1):
    num = np.bitwise_xor(V[w + 17], np.uint32(c0)).astype(np.uint32)
    numold = np.full(w.shape, np.uint32(c1), dtype=np.uint32)
    Mv = np.uint32(0xffffffff)
    for jw in range(16, 0, -1):
        idx1 = (num >> np.uint32(16)) & np.uint32(0xff)
        idx2 = (num >> np.uint32(24)) & np.uint32(0xff)
        idx3 = (num >> np.uint32(8)) & np.uint32(0xff)
        idx4 = num & np.uint32(0xff)
        eax = V[w + T1 + idx1].astype(np.uint32)
        eax = (eax + V[w + T0 + idx2].astype(np.uint32)) & Mv
        eax = eax ^ V[w + T2 + idx3].astype(np.uint32)
        eax = (eax + V[w + T3 + idx4].astype(np.uint32)) & Mv
        eax = eax ^ V[w + jw].astype(np.uint32)
        num, numold = (numold ^ eax).astype(np.uint32), num
    numold = numold ^ V[w + 0].astype(np.uint32)
    return numold, num
def valid_vec(p0, p1):
    a0 = p0 & np.uint32(0xff)
    ok = (a0 == 0) | (a0 == 1)
    for sh in (8, 16, 24):
        c = (p0 >> np.uint32(sh)) & np.uint32(0xff)
        ok &= (c >= np.uint32(0x30)) & (c <= np.uint32(0x39))
    for sh in (0, 8, 16):
        c = (p1 >> np.uint32(sh)) & np.uint32(0xff)
        ok &= (c >= np.uint32(0x30)) & (c <= np.uint32(0x39))
    return ok

scanned = 0; c4=c8=c16=cg=0
gbbq_hits=[]; feistel_hits=[]
CHUNK = 1 << 20; OVER = 4176
KEYLEN = 4176
for ri, (base, size, st, p, tp) in enumerate(regions):
    is_img = (st == MEM_IMAGE) or (tp & 0x1000000)
    off = 0; prev = b""
    while off < size:
        toread = min(CHUNK, size - off)
        buf = ctypes.create_string_buffer(toread); nr = ctypes.c_size_t(0)
        ok = k32.ReadProcessMemory(h, ctypes.c_void_p(base + off), buf, toread, ctypes.byref(nr))
        if ok and nr.value > 0:
            cur = buf.raw[:nr.value]
            scanned += len(cur)
            data = prev + cur
            baseabs = base + off - len(prev)
            if is_img:
                c4 += data.count(KEY16[:4])
                c8 += data.count(KEY16[:8])
                c16 += data.count(KEY16)
            st2 = 0
            while True:
                i = data.find(GBBQ_REC, st2)
                if i < 0: break
                gbbq_hits.append(baseabs + i)
                st2 = i + 1
            if is_img:
                mem = np.frombuffer(cur, dtype=np.uint8)
                for align in range(4):
                    nwords = (len(mem) - align) // 4
                    if nwords < KEYLEN // 4 + 1: continue
                    V = mem[align: align + nwords*4].view(np.uint32)
                    wmax = nwords - (KEYLEN // 4)
                    start = 0
                    while start < wmax:
                        w = np.arange(start, min(start + 50000, wmax), dtype=np.int64)
                        p0, p1 = parse_block_vec(V, w, C0, C1)
                        okv = valid_vec(p0, p1)
                        for wi in w[okv]:
                            abs_addr = base + off + align + int(wi) * 4
                            keybytes = V[int(wi):int(wi) + KEYLEN//4].view(np.uint8).tobytes()
                            feistel_hits.append((abs_addr, hashlib.sha256(keybytes).hexdigest()))
                        start += 50000
            prev = cur[-OVER:]
        else:
            prev = b""
        off += toread
    if (ri+1) % 50 == 0:
        print("progress region=%d/%d scanned=%.1fMB gbbq_hits=%d feistel_hits=%d" % (ri+1, len(regions), scanned/1048576, len(gbbq_hits), len(feistel_hits)), flush=True)
k32.CloseHandle(h)

print("TOTAL scanned=%.1fMB regions=%d" % (scanned/1048576, len(regions)))
print("IMAGE-region key prefix hits: 4B=%d 8B=%d 16B=%d" % (c4, c8, c16))
print("gbbq cipher hits=%d first20=%s" % (len(gbbq_hits), [hex(a) for a in gbbq_hits[:20]]))
print("feistel self-validated hits=%d" % len(feistel_hits))
for a, s in feistel_hits[:10]:
    print("F HIT addr=0x%x sha256=%s" % (a, s))

with open(OUTLOG, "w", encoding="utf-8") as f:
    f.write("I-9 full-scan diagnosis %s\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
    f.write("PID=%d\nreadable regions=%d total=%.1fMB\n" % (PID, len(regions), total/1048576))
    f.write("scanned=%.1fMB\n" % (scanned/1048576))
    f.write("IMAGE key prefix hits 4/8/16 = %d/%d/%d\n" % (c4, c8, c16))
    f.write("gbbq cipher hits=%d\n" % len(gbbq_hits))
    f.write("feistel self-validated hits=%d\n" % len(feistel_hits))
    for a, s in feistel_hits[:10]:
        f.write("F HIT addr=0x%x sha256=%s\n" % (a, s))
print("LOG=", OUTLOG)
