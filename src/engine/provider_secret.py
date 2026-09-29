"""UI BYOK 密钥的单机存储封装；Windows 使用当前用户 DPAPI。"""

import base64
import os


def protect(value: str) -> str:
    """返回可落盘的加密值；非 Windows 仅由文件权限保护。"""
    if os.name != "nt":
        return value
    return "dpapi-user-v1:" + base64.b64encode(_crypt(value.encode("utf-8"), True)).decode("ascii")


def unprotect(value: str) -> str:
    if not value.startswith("dpapi-user-v1:"):
        raise ValueError("供应商密钥格式无效")
    if os.name != "nt":
        raise RuntimeError("该密钥仅可在原 Windows 用户环境解密")
    try:
        encrypted = base64.b64decode(value.split(":", 1)[1], validate=True)
        return _crypt(encrypted, False).decode("utf-8")
    except Exception as exc:
        raise RuntimeError("供应商密钥解密失败，请在原 Windows 用户环境恢复或重新配置") from exc


def is_protected(value: str) -> bool:
    return value.startswith("dpapi-user-v1:")


def _crypt(data: bytes, encrypt: bool) -> bytes:
    """ctypes 调用 CryptProtectData/CryptUnprotectData，不经命令行暴露 Key。"""
    import ctypes
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

    source_buffer = ctypes.create_string_buffer(data)
    source = DataBlob(len(data), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = DataBlob()
    crypt32 = ctypes.WinDLL("Crypt32.dll", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [ctypes.POINTER(DataBlob), wintypes.LPCWSTR,
                                         ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                         wintypes.DWORD, ctypes.POINTER(DataBlob)]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(DataBlob), ctypes.c_void_p,
                                           ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                           wintypes.DWORD, ctypes.POINTER(DataBlob)]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    func = crypt32.CryptProtectData if encrypt else crypt32.CryptUnprotectData
    args = ((ctypes.byref(source), "HSK-AI-Coach BYOK", None, None, None, 1,
             ctypes.byref(output)) if encrypt else
            (ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)))
    if not func(*args):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32 = ctypes.WinDLL("Kernel32.dll", use_last_error=True)
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        kernel32.LocalFree(output.pbData)
