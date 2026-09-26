import ctypes
import os
from pathlib import Path
import sys
import tempfile


if sys.platform != "win32":
    print("Native delete-pending probe is Windows-only")
    raise SystemExit(0)

from ctypes import wintypes

kernel = ctypes.WinDLL("kernel32", use_last_error=True)
kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
kernel.CreateFileW.restype = wintypes.HANDLE
kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
kernel.CloseHandle.restype = wintypes.BOOL

with tempfile.TemporaryDirectory() as root:
    path = Path(root) / "synthetic-lock"
    path.mkdir()
    handle = kernel.CreateFileW(str(path), 0, 7, None, 3, 0x02000000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        os.rmdir(path)
        for label, operation in (("mkdir", lambda: os.mkdir(path)), ("stat", lambda: os.stat(path))):
            try:
                operation()
                print("DELETE_PENDING", label, "succeeded", flush=True)
            except OSError as error:
                print("DELETE_PENDING", label, type(error).__name__, "winerror", error.winerror, flush=True)
    finally:
        kernel.CloseHandle(handle)
    path.mkdir()
    path.rmdir()
    print("AFTER_HANDLE_CLOSE mkdir succeeded", flush=True)
