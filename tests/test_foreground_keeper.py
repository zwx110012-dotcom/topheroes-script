import threading
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from foreground_keeper import ForegroundKeeper
from crystal_gather import CrystalGatherer, GameSession, RunStopped
from rescue_watchdog import ProgressWatchdog
from top_heroes import WindowChanged, WindowUnavailable, WorkflowError


def fixture():
    clock = [0.]
    raw = SimpleNamespace(foreground_state=Mock(return_value='away'), rescue_focus=Mock())
    keeper = ForegroundKeeper(raw, clock=lambda:clock[0])
    poll = lambda: keeper.poll(lambda:None, lambda:None, lambda text:None)
    return clock, raw, keeper, poll


def test_exact_thirty_seconds_takeover_invalidates_old_click_targets():
    clock, raw, keeper, poll = fixture()
    poll()
    clock[0] = 29
    poll()
    raw.rescue_focus.assert_not_called()
    clock[0] = 30
    with pytest.raises(WindowChanged): poll()
    raw.rescue_focus.assert_called_once()


def test_returning_before_deadline_starts_new_continuous_interval():
    clock, raw, keeper, poll = fixture()
    poll()
    clock[0] = 20
    raw.foreground_state.return_value = 'front'
    poll()
    clock[0] = 25
    raw.foreground_state.return_value = 'away'
    poll()
    clock[0] = 50
    poll()
    raw.rescue_focus.assert_not_called()
    clock[0] = 55
    with pytest.raises(WindowChanged): poll()


def test_missing_game_cannot_trigger_foreground_takeover_or_launch():
    clock, raw, keeper, poll = fixture()
    poll()
    raw.foreground_state.return_value = 'missing'
    clock[0] = 35
    poll()
    assert keeper.away_since is None
    raw.rescue_focus.assert_not_called()


def test_focus_denial_retries_at_five_seconds_without_clicks_or_restart():
    clock, raw, keeper, poll = fixture()
    raw.rescue_focus.side_effect = WindowUnavailable('Windows denied focus')
    poll()
    for instant in (30,31,32,34,35):
        clock[0] = instant
        poll()
    assert raw.rescue_focus.call_count == 2
    assert keeper.away_since == 0


def test_f8_at_deadline_and_during_model_validation_prevents_takeover():
    clock, raw, keeper, poll = fixture()
    poll()
    clock[0] = 30
    with pytest.raises(RunStopped):
        keeper.poll(Mock(side_effect=RunStopped('F8')), lambda:None, lambda text:None)
    raw.rescue_focus.assert_not_called()
    stop = threading.Event()
    def cancel():
        if stop.is_set(): raise RunStopped('F8')
    with pytest.raises(RunStopped): keeper.poll(cancel, stop.set, lambda text:None)
    raw.rescue_focus.assert_not_called()


def test_changed_model_cannot_take_foreground():
    clock, raw, keeper, poll = fixture()
    poll()
    clock[0] = 30
    with pytest.raises(WorkflowError, match='model'):
        keeper.poll(lambda:None, Mock(side_effect=WorkflowError('model changed')), lambda text:None)
    raw.rescue_focus.assert_not_called()


@pytest.mark.parametrize('minimized,foreground,expected', [(False,17,'front'),(False,18,'away'),(True,17,'away')])
def test_native_foreground_observation_handles_minimized_game(minimized,foreground,expected):
    raw = GameSession.__new__(GameSession)
    raw.hwnd = 17
    with patch('win32gui.IsWindow', return_value=True), patch('win32gui.IsIconic', return_value=minimized), \
         patch('win32gui.GetForegroundWindow', return_value=foreground):
        assert raw.foreground_state() == expected


@pytest.mark.parametrize('disconnected', [False, True])
def test_real_worker_sleep_checks_focus_each_second_and_retains_pending_dispatch(disconnected):
    from connection_recovery import Disconnected
    clock = [0.]
    state = ['away']
    bot = CrystalGatherer.__new__(CrystalGatherer)
    bot.config = {'rescue_timeout_seconds':300}
    bot.stop_event = None
    bot.logger = Mock()
    bot.require_ready = Mock()
    bot._departure_pending = (1,{1})
    bot.completed = 3
    raw = GameSession.__new__(GameSession)
    raw.foreground_state = lambda:state[0]
    raw.rescue_focus = Mock(side_effect=lambda:state.__setitem__(0,'front'))
    raw.rebind = Mock()
    bot.session = raw
    def run():
        if state[0] == 'front': raise RunStopped('verified')
        if disconnected: raise Disconnected(None)
        raise WindowUnavailable('background')
    bot.run_continuous = run
    bot.connection_recovery = (SimpleNamespace(raw=raw, recover=lambda b:b.sleep(120)) if disconnected else None)
    def sleep(seconds):
        clock[0] += seconds
        assert clock[0] < 32
    with patch('time.monotonic', side_effect=lambda:clock[0]), patch('time.sleep', side_effect=sleep), \
         patch('rescue_watchdog.RescueController') as rescue, pytest.raises(RunStopped, match='verified'):
        bot.run_resilient()
    assert clock[0] == 30
    assert bot.completed == 3 and bot._departure_pending == (1,{1})
    assert bot.rescue_watchdog.last_progress == 0  # Focus alone is not mining progress.
    raw.rescue_focus.assert_called_once()
    rescue.return_value.recover.assert_not_called()


def test_stopped_task_never_polls_or_takes_focus_again():
    bot = CrystalGatherer.__new__(CrystalGatherer)
    bot.stop_event = threading.Event()
    bot.stop_event.set()
    bot.foreground_keeper = Mock()
    with pytest.raises(RunStopped): bot.check_stop()
    bot.foreground_keeper.poll.assert_not_called()
