"""Frozen/offline rescue checks. All game control is replaced with local fakes."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

from crystal_gather import CrystalGatherer, RunStopped
from queue_monitor import QueueRow
from rescue_watchdog import ProgressWatchdog, RescueController, RescueDue
from top_heroes import WindowUnavailable


def live_foreground(model, output):
    """Opt-in native focus test: one takeover, no collection or game clicks."""
    import threading
    import time
    import win32api
    from crystal_gather import GameSession
    from foreground_keeper import ForegroundKeeper
    from top_heroes import WindowChanged
    from yolo_detector import require_verified_model
    stop = threading.Event()
    result = dict(ok=False, frozen=bool(getattr(sys,'frozen',False)), game_clicked=False,
                  game_restarted=False)
    def cancel():
        if stop.is_set() or win32api.GetAsyncKeyState(0x77) & 0x8001:
            stop.set()
            raise RunStopped('F8 cancelled foreground test')
    def worker():
        started = time.monotonic()
        observed_away = None
        session = GameSession('Steam Top Heroes', stop_event=stop, defer_ready=True)
        keeper = ForegroundKeeper(session)
        try:
            while time.monotonic()-started < 50:
                try:
                    keeper.poll(cancel, lambda:require_verified_model(Path(model),capacity=4), lambda text:None)
                except WindowChanged:
                    if observed_away is None or session.foreground_state() != 'front':
                        raise AssertionError('未核实30秒后游戏确已回到前台')
                    elapsed = time.monotonic()-observed_away
                    if elapsed < 30:
                        raise AssertionError('接管早于30秒')
                    result.update(ok=True, foreground_restored=True, seconds_since_away=elapsed,
                                  worker_thread=True, stale_observation_invalidated=True)
                    return
                if keeper.away_since is not None:
                    observed_away = keeper.away_since
                stop.wait(.1)
            result['error'] = '50秒内未形成可完成的30秒离开前台区间；未宣称接管成功'
        except Exception as error:
            result['error'] = str(error)
    thread = threading.Thread(target=worker,daemon=True)
    thread.start()
    thread.join(55)
    if thread.is_alive():
        stop.set()
        thread.join(2)
        result.update(ok=False,error='测试超时，已请求取消')
    Path(output).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if result['ok'] else 1


def run(output):
    checks = []
    try:
        clock = [0.]
        queues = lambda idle=0, seconds=8000: [QueueRow(i,'idle') if i<=idle else
                     QueueRow(i,'mining',seconds+i) for i in range(1,5)]
        dog = ProgressWatchdog(300, lambda:clock[0])
        for now in range(0,7200,30):
            clock[0] = now
            dog.observe(queues(seconds=8000-now),4,4,None)
            dog.check()
        checks.append('normal_two_hour_mining_wait_no_rescue')
        dog.progress()
        clock[0] += 300
        dog.observe(queues(1),4,4,(1,{1}))
        try:
            dog.check()
        except RescueDue:
            checks.append('pending_departure_deadline_300_seconds')
        else:
            raise AssertionError('pending departure did not time out')
        events = []
        stop = [False]
        def check_stop():
            if stop[0]: raise RunStopped('F8')
        bot = SimpleNamespace(capacity=4, _departure_pending=(1,{1}), completed=7,
            require_ready=lambda:None, check_stop=check_stop, log=events.append,
            note_progress=dog.progress, ensure_world=lambda **kw:events.append('world'),
            stable_queues=lambda:queues(4))
        bot.reconcile_reconnected_departure = lambda r: CrystalGatherer.reconcile_reconnected_departure(bot,r)
        raw = SimpleNamespace(rescue_focus=lambda:events.append('focus'))
        controller = RescueController(raw)
        assert controller.recover(bot) == queues(4)
        assert bot._departure_pending is None and bot.completed == 7 and controller.control is None
        checks.append('all_idle_releases_pending_without_duplicate_depart_or_restart')
        bot._departure_pending = (1,{1})
        bot.stable_queues = lambda:queues()
        controller.recover(bot)
        controller.recover(bot)
        assert bot.completed == 8 and bot._departure_pending is None
        checks.append('confirmed_dispatch_counted_once')
        stop[0] = True
        before = len(events)
        try:
            controller.recover(bot)
        except RunStopped:
            pass
        else:
            raise AssertionError('F8 did not stop rescue')
        assert len(events) == before
        checks.append('f8_blocks_focus_and_restart')
        from foreground_keeper import ForegroundKeeper
        from top_heroes import WindowChanged
        stop[0] = False
        clock[0] = 0
        focus_calls = []
        foreground_raw = SimpleNamespace(foreground_state=lambda:'away',
            rescue_focus=lambda:focus_calls.append(clock[0]))
        keeper = ForegroundKeeper(foreground_raw, clock=lambda:clock[0])
        keeper.poll(check_stop, lambda:None, events.append)
        clock[0] = 29.9
        keeper.poll(check_stop, lambda:None, events.append)
        assert not focus_calls
        clock[0] = 30.5
        try:
            keeper.poll(check_stop, lambda:None, events.append)
        except WindowChanged:
            pass
        else:
            raise AssertionError('takeover did not invalidate old recognition')
        assert focus_calls == [30.5]
        stop[0] = True
        clock[0] = 100
        try:
            keeper.poll(check_stop, lambda:None, events.append)
        except RunStopped:
            pass
        assert focus_calls == [30.5]
        checks.append('foreground_30_seconds_and_f8_cancel_no_stale_click')
        # Import packaged native dependencies, but never invoke focus/close.
        from rescue_game import installed_game_paths
        assert installed_game_paths()
        checks.append('packaged_win32_and_steam_manifest_available')
        result = dict(ok=True, frozen=bool(getattr(sys,'frozen',False)), checks=checks,
                      real_input=False, real_game_restart=False)
    except Exception as error:
        result = dict(ok=False,error=str(error),checks=checks,real_input=False)
    output = Path(output)
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if result['ok'] else 1
