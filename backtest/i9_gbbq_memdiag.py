"""I-9 只读内存诊断（不改任何数据）：
1) 放宽保护过滤统计可读区；2) 统计公开 key 4/8/16 字节前缀命中；
3) 在内存中定位 gbbq 密文缓冲（用磁盘 gbbq 首 29B 记录做签名）；
4) 对密文缓冲邻近区做 4176B 高熵窗口候选枚举（仅登记地址/sha，不打印明文）。
"""
import ctypes, hashlib, sys, math
from ctypes import wintypes

PID = int(sys.argv[1]) if len(sys.argv) > 1 else 14192
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

MEM_COMMIT = 0x1000
def readable(prot):
    base = prot & 0x1F
    if prot & 0x01:  # NOACCESS
        return False
    if prot & 0x100:  # GUARD
        return False
    return base in (0x02, 0x04, 0x10, 0x20, 0x40)

KEY16 = bytes([56,167,194,29,224,106,23,226,209,57,162,64,156,186,70,175])
KEY4 = KEY16[:4]; KEY8 = KEY16[:8]
# 磁盘 gbbq 首条 29B 记录（signture 用前 12B 密文，启动后该缓冲在内存中应出现）
GBBQ_REC = bytes.fromhex("631224b0f311c9a953c0bb18")

addr = 0; mbi = MBI(); regions = 0; total = 0
c4=c8=c16=cg=0
gbbq_hits=[]
CHUNK=1<<20; OVER=4176
while addr < 0x7FFFFFFFFFFF:
    r=k32.VirtualQueryEx(h, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi))
    if r==0: break
    base=mbi.BaseAddress or 0; size=mbi.RegionSize or 0
    if size==0: break
    if mbi.State==MEM_COMMIT and readable(mbi.Protect) and base:
        regions+=1
        off=0; prev=b""
        while off<size:
            toread=min(CHUNK,size-off)
            buf=ctypes.create_string_buffer(toread); nr=ctypes.c_size_t(0)
            ok=k32.ReadProcessMemory(h, ctypes.c_void_p(base+off), buf, toread, ctypes.byref(nr))
            if ok and nr.value>0:
                cur=buf.raw[:nr.value]; total+=len(cur); data=prev+cur
                baseabs=base+off-len(prev)
                for sig,cnt_name in [(KEY4,'4'),(KEY8,'8'),(KEY16,'16')]:
                    st=0
                    while True:
                        i=data.find(sig,st)
                        if i<0: break
                        if cnt_name=='4': c4+=1
                        elif cnt_name=='8': c8+=1
                        else: c16+=1
                        st=i+1
                st=0
                while True:
                    i=data.find(GBBQ_REC,st)
                    if i<0: break
                    gbbq_hits.append(baseabs+i)
                    st=i+1
                prev=cur[-OVER:]
            else:
                prev=b""
            off+=toread
    nxt=base+size
    if nxt<=addr: break
    addr=nxt
k32.CloseHandle(h)
print("regions=",regions,"bytes=",total)
print("key_prefix_hits: 4B=%d 8B=%d 16B=%d"%(c4,c8,c16))
print("gbbq_cipher_hits=",len(gbbq_hits))
for a in gbbq_hits[:20]:
    print("GBBQ_HIT addr=0x%x"%a)
