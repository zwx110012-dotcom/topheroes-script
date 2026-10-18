"""Prefer current Windows C++ runtime over old Anaconda-local DLLs, in this process only."""
import ctypes
import os
import sys
from pathlib import Path


def preload_cpp_runtime():
    if os.name != "nt":
        return
    directory = ctypes.create_unicode_buffer(32768)
    length = ctypes.windll.kernel32.GetSystemDirectoryW(directory, len(directory))
    if not 0 < length < len(directory):
        raise OSError("无法定位 Windows C++ 运行库")
    folders = [Path(getattr(sys, "_MEIPASS", directory.value)), Path(directory.value)]
    for filename in ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll"):
        path = next((folder / filename for folder in folders if (folder / filename).is_file()), None)
        if path:
            ctypes.WinDLL(str(path))


preload_cpp_runtime()
