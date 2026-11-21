"""生成 Windows 桌面快捷方式（.lnk）。

使用 Windows 官方 IShellLinkW + IPersistFile COM 接口（经 ctypes 调用 Win32 API），
由 Shell 自身写出 .lnk，保证 IDList/LinkInfo/图标索引完全合规。
不依赖 pywin32，也不使用 WScript.Shell（后者可执行任意命令，风险更高）。

用法: python scripts/create-aiedu-shortcut.py
"""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

CLSCTX_INPROC_SERVER = 1
SW_SHOWMINNOACTIVE = 7

# IShellLinkW vtable 索引（0-2 为 IUnknown）
SL_SET_DESCRIPTION = 7
SL_SET_WORKING_DIRECTORY = 9
SL_SET_ARGUMENTS = 11
SL_SET_SHOW_CMD = 15
SL_SET_ICON_LOCATION = 17
SL_SET_PATH = 20
PF_SAVE = 6
PF_RELEASE = 2


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def make_guid(text: str) -> GUID:
    import uuid
    u = uuid.UUID(text)
    g = GUID()
    g.Data1 = u.time_low
    g.Data2 = u.time_mid
    g.Data3 = u.time_hi_version
    for i, b in enumerate(u.bytes[8:]):
        g.Data4[i] = b
    return g


CLSID_SHELL_LINK = make_guid("00021401-0000-0000-C000-000000000046")
IID_ISHELL_LINK_W = make_guid("000214F9-0000-0000-C000-000000000046")
IID_IPERSIST_FILE = make_guid("0000010B-0000-0000-C000-000000000046")

HRESULT = ctypes.c_long
ONE_STR = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, wintypes.LPCWSTR)
ONE_INT = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, ctypes.c_int)
STR_INT = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, wintypes.LPCWSTR, ctypes.c_int)
QUERY_INTERFACE = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p,
                                     ctypes.POINTER(GUID),
                                     ctypes.POINTER(ctypes.c_void_p))
SAVE = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, wintypes.LPCWSTR, wintypes.BOOL)
RELEASE = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)


def vtable_method(obj: int, index: int, proto):
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return proto(vtbl[index])


def check(hr: int, what: str) -> None:
    if hr != 0:
        raise RuntimeError(f"{what} failed: HRESULT 0x{hr & 0xFFFFFFFF:08x}")


def get_desktop() -> str:
    buf = ctypes.create_unicode_buffer(260)
    fn = ctypes.windll.shell32.SHGetFolderPathW
    fn.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.HANDLE,
                   wintypes.DWORD, wintypes.LPWSTR]
    fn.restype = HRESULT
    check(fn(0, 0x0010, 0, 0, buf), "SHGetFolderPathW")
    return buf.value


def main() -> None:
    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    github_root = os.path.dirname(scripts_dir)
    launcher = os.path.join(scripts_dir, "start-aiedu.ps1")
    icon = os.path.join(os.environ["LOCALAPPDATA"], "AI Edu 2.0", "ai-edu.ico")
    for p in (launcher, icon):
        if not os.path.isfile(p):
            raise SystemExit(f"missing prerequisite: {p}")

    target = os.path.join(os.environ["WINDIR"], "System32",
                          "WindowsPowerShell", "v1.0", "powershell.exe")
    args = ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "'
            + launcher + '"')
    description = "启动 AI Edu 2.0（默认开启付费真实生成）"
    lnk = os.path.join(get_desktop(), "AI Edu 2.0.lnk")

    ole32 = ctypes.windll.ole32
    ole32.CoInitialize.argtypes = [ctypes.c_void_p]
    ole32.CoInitialize(None)
    co_create = ole32.CoCreateInstance
    co_create.argtypes = [ctypes.POINTER(GUID), ctypes.c_void_p, wintypes.DWORD,
                          ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
    co_create.restype = HRESULT

    link = ctypes.c_void_p()
    check(co_create(ctypes.byref(CLSID_SHELL_LINK), None, CLSCTX_INPROC_SERVER,
                    ctypes.byref(IID_ISHELL_LINK_W), ctypes.byref(link)),
          "CoCreateInstance(CLSID_ShellLink)")
    try:
        check(vtable_method(link.value, SL_SET_PATH, ONE_STR)(link, target), "SetPath")
        check(vtable_method(link.value, SL_SET_ARGUMENTS, ONE_STR)(link, args), "SetArguments")
        check(vtable_method(link.value, SL_SET_WORKING_DIRECTORY, ONE_STR)(link, github_root),
              "SetWorkingDirectory")
        check(vtable_method(link.value, SL_SET_DESCRIPTION, ONE_STR)(link, description),
              "SetDescription")
        check(vtable_method(link.value, SL_SET_SHOW_CMD, ONE_INT)(link, SW_SHOWMINNOACTIVE),
              "SetShowCmd")
        check(vtable_method(link.value, SL_SET_ICON_LOCATION, STR_INT)(link, icon, 0),
              "SetIconLocation")

        persist = ctypes.c_void_p()
        check(vtable_method(link.value, 0, QUERY_INTERFACE)(
            link, ctypes.byref(IID_IPERSIST_FILE), ctypes.byref(persist)),
            "QueryInterface(IPersistFile)")
        try:
            check(vtable_method(persist.value, PF_SAVE, SAVE)(persist, lnk, True),
                  "IPersistFile::Save")
        finally:
            vtable_method(persist.value, PF_RELEASE, RELEASE)(persist)
    finally:
        vtable_method(link.value, PF_RELEASE, RELEASE)(link)

    print("written:", lnk)
    print("  target :", target)
    print("  args   :", args)
    print("  workdir:", github_root)
    print("  icon   :", icon)
    print("  bytes  :", os.path.getsize(lnk))


if __name__ == "__main__":
    main()
