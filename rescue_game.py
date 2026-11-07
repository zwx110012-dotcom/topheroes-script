"""Narrow Windows game control used only after the rescue deadline."""
import ctypes
from ctypes import wintypes
from pathlib import Path
import re
import time
import winreg

import win32con
import win32gui
import win32process

from game_launcher import GAME_EXE, game_is_running, image_is_running, request_launch
from top_heroes import WindowUnavailable, WorkflowError, game_windows


def installed_game_paths():
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam') as key:
        root = Path(winreg.QueryValueEx(key, 'SteamPath')[0])
    roots = {root}
    libraries = root/'steamapps/libraryfolders.vdf'
    if libraries.is_file():
        text = libraries.read_text(encoding='utf-8')
        roots.update(Path(p.replace('\\\\', '\\')) for p in re.findall(r'"path"\s+"([^"]+)"', text))
    paths = set()
    for library in roots:
        manifest = library/'steamapps/appmanifest_4007740.acf'
        if not manifest.is_file():
            continue
        match = re.search(r'"installdir"\s+"([^"]+)"', manifest.read_text(encoding='utf-8'))
        if match:
            folder = library/'steamapps/common'/match[1]
            paths.add(str((folder/'TopHeroes_Steam'/GAME_EXE).resolve()).casefold())
    if not paths:
        raise WorkflowError('未找到 Steam 4007740 安装清单；不接管未知游戏进程')
    return paths


class VerifiedGame:
    """Hold the OS process handle across checks to avoid PID reuse."""
    def __init__(self, hwnd):
        self.hwnd = hwnd
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        k = self.kernel
        k.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        k.OpenProcess.restype = wintypes.HANDLE
        k.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                ctypes.POINTER(wintypes.DWORD))
        k.QueryFullProcessImageNameW.restype = wintypes.BOOL
        k.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        k.WaitForSingleObject.restype = wintypes.DWORD
        k.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
        k.TerminateProcess.restype = wintypes.BOOL
        k.CloseHandle.argtypes = (wintypes.HANDLE,)
        k.CloseHandle.restype = wintypes.BOOL
        k.GetCurrentThreadId.argtypes = ()
        k.GetCurrentThreadId.restype = wintypes.DWORD
        self.pid = win32process.GetWindowThreadProcessId(hwnd)[1]
        self.handle = k.OpenProcess(0x1000 | 0x100000 | 1, False, self.pid)
        if not self.handle:
            raise WindowUnavailable('无法取得游戏进程句柄；未操作')
        buffer = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buffer))
        try:
            if not k.QueryFullProcessImageNameW(self.handle, 0, buffer, ctypes.byref(size)):
                raise WindowUnavailable('无法核实游戏程序路径；未操作')
            if str(Path(buffer.value).resolve()).casefold() not in installed_game_paths():
                raise WorkflowError('游戏窗口不属于 Steam 4007740 的安装程序；拒绝接管')
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def running(self):
        result = self.kernel.WaitForSingleObject(self.handle, 0)
        if result not in (0, 258):
            raise WindowUnavailable('无法核实游戏进程状态；未操作')
        return result == 258

    def require_window(self):
        if (not self.running() or not win32gui.IsWindow(self.hwnd) or
                win32process.GetWindowThreadProcessId(self.hwnd)[1] != self.pid):
            raise WindowUnavailable('游戏窗口或进程已变化；重新核实后才能操作')

    def focus(self, check_stop):
        self.require_window()
        check_stop()
        win32gui.ShowWindow(self.hwnd, win32con.SW_RESTORE)
        # A collection worker may never have created a Windows message queue.
        # AttachThreadInput requires one; PeekMessage creates it without taking
        # or removing any message from the GUI/hotkey threads.
        win32gui.PeekMessage(None, 0, 0, win32con.PM_NOREMOVE)
        # Attach only our worker input queue to the current foreground thread;
        # detach in finally. Never inject Alt/Esc or click another application.
        current = self.kernel.GetCurrentThreadId()
        foreground = win32gui.GetForegroundWindow()
        thread = win32process.GetWindowThreadProcessId(foreground)[0] if foreground else 0
        attached = False
        try:
            if thread and thread != current:
                win32process.AttachThreadInput(current, thread, True)
                attached = True
            self.require_window()
            check_stop()
            win32gui.SetForegroundWindow(self.hwnd)
        finally:
            if attached:
                win32process.AttachThreadInput(current, thread, False)
        if win32gui.GetForegroundWindow() != self.hwnd:
            raise WindowUnavailable('Windows 暂未允许切回游戏；保持暂停点击')


def unique_game():
    windows = game_windows()
    if not windows:
        raise WindowUnavailable('游戏窗口尚未出现')
    if len(windows) != 1:
        raise WorkflowError('游戏窗口不唯一；不接管或关闭任何窗口')
    return VerifiedGame(windows[0][0])


class GameControl:
    def restart(self, bot):
        bot.check_stop()
        if game_is_running():
            with unique_game() as game:
                game.require_window()
                bot.check_stop()
                win32gui.PostMessage(game.hwnd, win32con.WM_CLOSE, 0, 0)
                deadline = time.monotonic()+20
                while game.running() and time.monotonic() < deadline:
                    bot.sleep(.5)
                if game.running():
                    bot.check_stop()
                    bot.log('游戏20秒未正常退出；仅结束已核实路径和句柄的游戏进程。')
                    if not game.kernel.TerminateProcess(game.handle, 1):
                        raise WindowUnavailable('游戏进程拒绝退出；不重复启动')
        # The Steam proxy wrapper restores patched modules after its child exits.
        # Never kill that wrapper or start another while restoration is pending.
        deadline = time.monotonic()+60
        bot.sleep(5)
        while game_is_running() or image_is_running('TopHeroesProxy.exe'):
            if time.monotonic() >= deadline:
                raise WindowUnavailable('旧游戏或专用代理仍未清理完成；不重复启动')
            bot.sleep(1)
        bot.check_stop()
        if request_launch():
            bot.log('救援已通过 Steam 请求启动游戏；等待主页和队伍加载，不以进程出现认定成功。')
