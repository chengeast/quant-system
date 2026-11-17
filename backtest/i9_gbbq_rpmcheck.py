"""验证 ReadProcessMemory 读到的是真实字节：读 tdxw.exe 模块基址 PE 头 MZ + 搜已知字符串。"""
import ctypes
from ctypes import wintypes
PID=14192
k32=ctypes.WinDLL("kernel32",use_last_error=True)
k32.OpenProcess.restype=wintypes.HANDLE
k32.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
h=k32.OpenProcess(0x0410,False,PID)
print("handle=",h)

TH32CS_SNAPMODULE=0x08
class MOD(ctypes.Structure):
    _fields_=[("dwSize",wintypes.DWORD),("th32ModuleID",wintypes.DWORD),("th32ProcessID",wintypes.DWORD),
              ("GlblcntUsage",wintypes.DWORD),("ProccntUsage",wintypes.DWORD),("modBaseAddr",ctypes.c_void_p),
              ("modBaseSize",wintypes.DWORD),("hModule",wintypes.HANDLE),("szModule",ctypes.c_char*256),
              ("szExePath",ctypes.c_char*260)]
m=MOD(); m.dwSize=ctypes.sizeof(MOD)
snap=k32.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE|0x10,PID)
k32.Module32First.argtypes=[wintypes.HANDLE,ctypes.POINTER(MOD)]
ok=k32.Module32First(snap,ctypes.byref(m))
base=None
while ok:
    name=m.szModule.split(b'\0')[0].decode('latin1')
    if name.lower()=='tdxw.exe':
        base=m.modBaseAddr; print("tdxw base=0x%x size=%d path=%s"%(base,m.modBaseSize,m.szExePath.split(b'\0')[0].decode('latin1')))
        break
    ok=k32.Module32First  # placeholder
    ok=k32.Module32Next(snap,ctypes.byref(m))
k32.CloseHandle(snap)

k32.ReadProcessMemory.argtypes=[wintypes.HANDLE,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_size_t,ctypes.POINTER(ctypes.c_size_t)]
k32.ReadProcessMemory.restype=wintypes.BOOL
if base:
    buf=ctypes.create_string_buffer(64); nr=ctypes.c_size_t(0)
    ok=k32.ReadProcessMemory(h,ctypes.c_void_p(base),buf,64,ctypes.byref(nr))
    print("read PE ok=",ok,"n=",nr.value,"head=",buf.raw[:8].hex(),"ascii=",buf.raw[:2])
k32.CloseHandle(h)
