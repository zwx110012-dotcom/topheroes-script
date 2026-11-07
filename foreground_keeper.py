"""Foreground-only deadline; runs in the active collection worker."""
import time

from pywintypes import error as Win32Error
from top_heroes import WindowChanged, WindowUnavailable


class ForegroundKeeper:
    def __init__(self, raw, seconds=30, clock=None):
        self.raw, self.seconds = raw, seconds
        self.clock = clock or time.monotonic
        self.away_since = None
        self.next_poll = float('-inf')
        self.last_attempt = float('-inf')

    def poll(self, check_cancel, require_ready, log):
        check_cancel()
        now = self.clock()
        if now < self.next_poll:
            return
        self.next_poll = now + .5
        try:
            state = self.raw.foreground_state()
        except (WindowUnavailable, Win32Error):
            state = 'missing'
        if state != 'away':
            # A return to the foreground or a missing game breaks the interval.
            # Missing game recovery remains subject to the five-minute watchdog.
            self.away_since = None
            return
        if self.away_since is None:
            self.away_since = now
            log('游戏已离开前台；暂停点击，连续30秒未返回将自动切回。F8可取消。')
        if now-self.away_since < self.seconds or now-self.last_attempt < 5:
            return
        require_ready()
        check_cancel()
        self.last_attempt = now
        try:
            self.raw.rescue_focus()
        except (WindowUnavailable, Win32Error) as error:
            log(f'30秒前台接管暂未完成：{error}；5秒后重试，F8可停止。')
            return
        self.away_since = None
        log('游戏连续30秒不在前台，已自动切回；丢弃旧画面，重新识别后继续原任务。')
        # A focus change may occur between recognition and mouse-down. Unwind
        # that partial workflow so no pre-takeover coordinates can be reused.
        raise WindowChanged('前台已接管；需重新截图核实队伍和按钮')
