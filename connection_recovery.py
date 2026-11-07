"""Recognize the known connection dialog; resume only from fresh game state."""
import time
from pathlib import Path

import cv2
import numpy as np
from pywintypes import error as Win32Error

from top_heroes import Match, WorkflowError, WindowChanged, WindowUnavailable, load_template


class Disconnected(WorkflowError):
    def __init__(self, frame):
        super().__init__('检测到连接服务器失败弹窗；暂停采集并等待重连')
        self.frame = frame


class DisconnectDialog:
    def __init__(self, base):
        folder = Path(base) / 'templates' / 'connection'
        self.message = cv2.cvtColor(load_template(folder / 'connection_failed.png'), cv2.COLOR_BGR2GRAY)
        self.button = cv2.cvtColor(load_template(folder / 'confirm.png'), cv2.COLOR_BGR2GRAY)

    def match(self, frame):
        # These real UI assets are verified at the helper's standard client size.
        # A generic blue Confirm button alone never authorizes a restart.
        if frame.shape[:2] != (900, 1600):
            return None
        left, top, right, bottom = 512, 324, 1088, 522
        region = cv2.cvtColor(frame[top:bottom, left:right], cv2.COLOR_BGR2GRAY)
        response = cv2.matchTemplate(region, self.message, cv2.TM_CCOEFF_NORMED)
        _, score, _, point = cv2.minMaxLoc(response)
        if not np.isfinite(score) or score < .97:
            return None
        x, y = left + point[0], top + point[1]
        # The button must match independently at its dialog-relative position.
        bx, by = x + 46, y + 79
        h, w = self.button.shape
        if not (0 <= bx-4 and 0 <= by-4 and bx+w+4 <= 1600 and by+h+4 <= 900):
            return None
        crop = cv2.cvtColor(frame[by-4:by+h+4, bx-4:bx+w+4], cv2.COLOR_BGR2GRAY)
        _, button_score, _, offset = cv2.minMaxLoc(cv2.matchTemplate(crop, self.button, cv2.TM_CCOEFF_NORMED))
        if not np.isfinite(button_score) or button_score < .97:
            return None
        return Match(min(score, button_score), bx-4+offset[0], by-4+offset[1], w, h)


class GuardedSession:
    """Abort a partial workflow before interpreting pixels behind the dialog."""
    def __init__(self, raw, dialog):
        self.raw, self.dialog = raw, dialog

    def __getattr__(self, name):
        return getattr(self.raw, name)

    def capture(self):
        frame = self.raw.capture()
        if self.dialog.match(frame) is not None:
            raise Disconnected(frame)
        return frame


class ConnectionRecovery:
    def __init__(self, base, raw):
        self.raw = raw
        self.dialog = DisconnectDialog(base)

    def recover(self, bot, deadline=None, take_focus=False):
        from crystal_gather import QueueUnavailable
        from visual_navigation import VisualNavigator, PageUnavailable
        navigator = VisualNavigator(bot)
        previous = None
        last_click = float('-inf')
        last_log = time.monotonic()
        steam_restart = None
        last_focus = float('-inf')
        bot.log('检测到断线：暂停采集；自动确认重试，等待游戏恢复。F8可随时停止。')
        while True:
            bot.check_stop()
            if deadline is not None and time.monotonic() >= deadline:
                raise WindowUnavailable('救援重启后3分钟仍未确认世界页面及全部队伍')
            try:
                rebind = getattr(self.raw, 'rebind', None)
                if callable(rebind):
                    rebind()
                frame = self.raw.capture()
            except (WindowUnavailable, Win32Error):
                # During a restart, the window can disappear, be minimized, or
                # lose foreground. Wait without stealing focus or clicking.
                previous = None
                if take_focus and time.monotonic()-last_focus >= 10:
                    last_focus = time.monotonic()
                    try:
                        self.raw.rescue_focus()
                    except (WindowUnavailable, Win32Error):
                        pass
                if getattr(self.raw, 'allow_steam_restart', False):
                    if steam_restart is None:
                        from steam_restart import SteamRestartGuard
                        steam_restart = SteamRestartGuard(bot.log)
                    steam_restart.poll(bot.check_stop)
                if time.monotonic()-last_log >= 30:
                    bot.log('等待游戏窗口恢复并回到前台；暂停点击，F8可停止。')
                    last_log = time.monotonic()
                bot.sleep(1)
                continue
            generation = getattr(self.raw, 'generation', 0)
            button = self.dialog.match(frame)
            now = time.monotonic()
            if button is not None:
                key = ('disconnect', generation, button.center)
                stable = (previous and previous[:2] == key[:2] and
                          max(abs(a-b) for a,b in zip(previous[2], key[2])) <= 4)
                if stable and now-last_click >= 30:
                    bot.require_ready()
                    bot.check_stop()
                    try:
                        self.raw.click(button.center)
                    except WindowChanged:
                        previous = None
                        continue
                    except (WindowUnavailable, Win32Error):
                        previous = None
                        bot.sleep(1)
                        continue
                    last_click = time.monotonic()
                    bot.log('已确认断线弹窗并点击重试；等待重载，不执行采集或出发。')
                    previous = None
                    bot.sleep(2)
                    continue
                previous = key
            elif now-last_click >= 5:
                page = navigator.observe(frame)
                key = (page.name, generation)
                if page.name in ('城内', '世界地图') and previous == key:
                    # Readiness requires a stable known page AND the complete
                    # three-frame queue check. Never replay the interrupted
                    # depart button or reuse its pre-disconnect idle counts.
                    bot.require_ready()
                    try:
                        if page.name == '城内':
                            bot.ensure_world()
                        rows = bot.stable_queues()
                        if ({row.number for row in rows} != set(range(1,bot.capacity+1)) or
                            any(row.state not in ('idle','returning','mining','outbound','busy') or
                                (row.state != 'idle' and row.seconds is None) for row in rows)):
                            raise QueueUnavailable('重连后队伍状态或计时尚未完整恢复')
                    except (Disconnected, QueueUnavailable, PageUnavailable,
                            WindowUnavailable, Win32Error):
                        previous = None
                        bot.sleep(1)
                        continue
                    bot.check_stop()
                    bot.log('重连完成：已重新确认世界页面及全部队伍，继续原采集任务。')
                    return rows
                previous = key if page.name in ('城内', '世界地图') else None
            else:
                previous = None
            if now-last_log >= 30:
                bot.log('仍在等待游戏重连；暂停派队，F8可停止。')
                last_log = now
            bot.sleep(.5)
