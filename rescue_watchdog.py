"""A progress deadline, not a mouse-idle timer. No actions on a timer thread."""
import time

from top_heroes import WorkflowError, WindowUnavailable


class RescueDue(WorkflowError):
    pass


class ProgressWatchdog:
    def __init__(self, seconds=300, clock=None):
        self.seconds = seconds
        self.clock = clock or time.monotonic
        self.last_progress = self.clock()
        self.suspended = False
        self.previous = None

    def progress(self):
        self.last_progress = self.clock()

    def check(self):
        if not self.suspended and self.clock()-self.last_progress >= self.seconds:
            raise RescueDue(f'连续{self.seconds:g}秒未确认采集进展，启动超时救援')

    def observe(self, rows, capacity, target, pending):
        # A pending departure or a vacant slot still needs a result. Countdown
        # movement in other teams must not mask that failure indefinitely.
        if pending or not complete_rows(rows, capacity):
            return
        if sum(r.state != 'idle' for r in rows) < target:
            return
        current = {r.number: (r.state, r.seconds) for r in rows}
        if self.previous is None or any(
                number not in self.previous or state != self.previous[number][0] or
                (seconds is not None and self.previous[number][1] is not None and
                 seconds < self.previous[number][1])
                for number, (state, seconds) in current.items()):
            self.progress()
        self.previous = current


def complete_rows(rows, capacity):
    return (len(rows) == capacity and
            {r.number for r in rows} == set(range(1, capacity+1)) and
            all(r.state in ('idle', 'returning', 'mining', 'outbound', 'busy') and
                (r.state == 'idle' or r.seconds is not None) for r in rows))


class RescueController:
    def __init__(self, raw, clock=None, control=None):
        self.raw = raw
        self.clock = clock or time.monotonic
        self.control = control
        self.last_restart = float('-inf')

    def recover(self, bot):
        from crystal_gather import QueueUnavailable, TemplateTimeout
        from connection_recovery import ConnectionRecovery, Disconnected
        from visual_navigation import PageUnavailable
        from pywintypes import error as Win32Error
        recoverable = (WindowUnavailable, Win32Error, PageUnavailable,
                       QueueUnavailable, TemplateTimeout, Disconnected)
        bot.check_stop()
        bot.require_ready()  # A changed/unverified model never authorizes input.
        bot.log('超时救援：先切回游戏、恢复世界页面并重新核实全部队伍；F8可停止。')
        try:
            self.raw.rescue_focus()
            bot.ensure_world(reset=True)
            rows = bot.stable_queues()
            if not complete_rows(rows, bot.capacity):
                raise QueueUnavailable('救援后队伍状态尚未完整确认')
            if bot._departure_pending and not bot.reconcile_reconnected_departure(rows):
                raise QueueUnavailable('救援后原出发结果仍不明确')
            bot.log('页面救援完成：已核实全部队伍，继续原采集任务。')
            return rows
        except recoverable as error:
            bot.log(f'页面救援未完成：{error}')

        # A persistent bad screen must not cause a rapid restart loop. All waits
        # stay inside the original worker and honor its original stop event.
        wait = max(0, 600-(self.clock()-self.last_restart))
        if wait:
            bot.log(f'距离上次重启不足10分钟；等待{wait:.0f}秒，F8可停止。')
            bot.sleep(wait)
        bot.check_stop()
        if self.control is None:
            from rescue_game import GameControl
            self.control = GameControl()
        self.last_restart = self.clock()
        bot.log('升级救援：关闭已核实的游戏进程，等待专用代理清理后经 Steam 重启。')
        self.control.restart(bot)
        recovery = bot.connection_recovery or ConnectionRecovery(bot.base, self.raw)
        rows = recovery.recover(bot, deadline=self.clock()+180, take_focus=True)
        if bot._departure_pending and not bot.reconcile_reconnected_departure(rows):
            raise QueueUnavailable('重启后出发结果仍有歧义，保持暂停派队并继续复查')
        bot.log('重启救援完成：世界页面、全部队伍及原出发记录均已核实，继续采集。')
        return rows
