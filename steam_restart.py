"""Dismiss only Top Heroes' Steam already-running error, then retry once ready."""
from dataclasses import dataclass
import re
import time


@dataclass
class Dialog:
    window: int
    button_id: tuple
    text: tuple
    button: object = None


def matches_error(names):
    text = ' '.join(names).casefold()
    return bool((re.search(r'top\s*heroes', text) or '王国之歌' in text) and
                ('游戏已在运行' in text or 'game already running' in text))


class SteamDialogs:
    """Use accessible button invocation; no screen coordinates or global keys."""
    def __init__(self):
        import comtypes
        import comtypes.client
        # The caller owns a dedicated launch/recovery worker thread.
        comtypes.CoInitialize()
        from comtypes.gen import UIAutomationClient as UIA
        self.UIA = UIA
        self.client = comtypes.client.CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation)

    def _windows(self):
        import ctypes
        from ctypes import wintypes
        from pathlib import Path
        import win32gui
        import win32process
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam') as key:
            steam = Path(winreg.QueryValueEx(key, 'SteamPath')[0]).resolve()
        allowed = {str(steam/'steam.exe').casefold(),
                   str(steam/'bin/cef/cef.win64/steamwebhelper.exe').casefold()}
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD,wintypes.BOOL,wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE,wintypes.DWORD,wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD))
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        windows = []
        def visit(hwnd, unused):
            if not win32gui.IsWindowVisible(hwnd) or win32gui.IsIconic(hwnd):
                return
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            process = kernel.OpenProcess(0x1000,False,pid)
            if not process:
                return
            try:
                buffer = ctypes.create_unicode_buffer(32768)
                size = wintypes.DWORD(len(buffer))
                if kernel.QueryFullProcessImageNameW(process,0,buffer,ctypes.byref(size)):
                    if buffer.value.casefold() in allowed:
                        windows.append(hwnd)
            finally:
                kernel.CloseHandle(process)
        win32gui.EnumWindows(visit,None)
        return windows

    def _read(self, hwnd):
        root = self.client.ElementFromHandle(hwnd)
        elements = root.FindAll(self.UIA.TreeScope_Descendants,self.client.CreateTrueCondition())
        if elements.Length > 1500:
            return None
        names, buttons = [], []
        for index in range(elements.Length):
            element = elements.GetElement(index)
            name = element.CurrentName or ''
            if element.CurrentIsOffscreen:
                continue
            names.append(name)
            if (element.CurrentControlType == self.UIA.UIA_ButtonControlTypeId and
                    name.strip().casefold() in ('关闭','close') and element.CurrentIsEnabled):
                buttons.append(element)
        if not matches_error(names) or len(buttons) != 1:
            return None
        button = buttons[0]
        return Dialog(hwnd,tuple(button.GetRuntimeId()),tuple(names),button)

    def find(self):
        found = []
        for hwnd in self._windows():
            try:
                dialog = self._read(hwnd)
                if dialog:
                    found.append(dialog)
            except Exception:
                # An unavailable accessibility provider never authorizes input.
                continue
        return found[0] if len(found) == 1 else None

    def close(self, observed):
        if observed.window not in self._windows():
            return False
        fresh = self._read(observed.window)
        if not fresh or fresh.button_id != observed.button_id or not matches_error(fresh.text):
            return False
        pattern = fresh.button.GetCurrentPattern(self.UIA.UIA_InvokePatternId)
        pattern.QueryInterface(self.UIA.IUIAutomationInvokePattern).Invoke()
        return True


class SteamRestartGuard:
    def __init__(self, log, backend=None, running=None, launch=None, clock=time.monotonic):
        if running is None or launch is None:
            from game_launcher import game_is_running, request_launch
            running = running or game_is_running
            launch = launch or request_launch
        self.log, self.backend = log, backend
        self.running, self.launch, self.clock = running, launch, clock
        self.pending = False
        self.next_poll = 0
        self.retry_after = 0
        self.last_retry = float('-inf')
        self.attempts = 0
        self.closed_at = 0
        self.last_close = float('-inf')
        self.disabled = False

    def poll(self, check_stop=lambda:None):
        check_stop()
        now = self.clock()
        if self.disabled or now < self.next_poll:
            return
        self.next_poll = now + 2
        try:
            if self.backend is None:
                self.backend = SteamDialogs()
            dialog = self.backend.find()
            if dialog is not None:
                if now-self.last_close < 30:
                    return
                check_stop()
                if not self.backend.close(dialog):
                    return
                self.last_close = now
                self.pending = True
                self.closed_at = now
                self.retry_after = now + 5
                self.log('已关闭 Steam 的 Top Heroes“游戏已在运行”提示；等待旧进程退出后重新开始游戏。')
                return
            if not self.pending or now < self.retry_after:
                return
            check_stop()
            if self.running():
                if now-self.closed_at >= 30:
                    self.pending = False
                    self.log('游戏进程仍在运行；取消重复启动，继续等待游戏恢复。')
                return
            if self.attempts >= 3:
                self.pending = False
                self.log('Steam 自动重启已重试三次；保留采集任务等待，避免连续弹窗。')
                return
            if now-self.last_retry < 30:
                return
            check_stop()
            self.pending = False
            if self.launch():
                self.attempts += 1
                self.last_retry = now
                self.log('已重新通过 Steam 开始 Top Heroes；重连完成前暂停采集。')
        except Exception as error:
            # Stop requests must propagate; this guard only handles UI/provider
            # errors, never cancellation raised by the owner.
            from crystal_gather import RunStopped
            if isinstance(error, RunStopped):
                raise
            self.pending = False
            self.disabled = True
            self.log('Steam 弹窗自动处理不可用，继续等待游戏恢复：' + str(error))
