"""Reproduce the overnight pending departure with virtual time, without input."""
import threading
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from crystal_gather import CrystalGatherer, GameSession, QueueUnavailable, RunStopped
from queue_monitor import QueueRow
from rescue_watchdog import ProgressWatchdog, RescueController, RescueDue, complete_rows
from top_heroes import WindowUnavailable, WorkflowError
import test_crystal_gather as helpers


def rows(idle=0, seconds=7200, capacity=4):
    return [QueueRow(i, 'idle') if i <= idle else QueueRow(i, 'mining', seconds+i)
            for i in range(1, capacity+1)]


def test_five_minutes_means_no_confirmed_progress_not_no_mouse_click():
    clock = [0.]
    dog = ProgressWatchdog(300, lambda: clock[0])
    for now in range(0, 7200, 30):
        clock[0] = now
        dog.observe(rows(seconds=8000-now), 4, 4, None)
        dog.check()
    clock[0] += 299
    dog.check()
    clock[0] += 1
    with pytest.raises(RescueDue):
        dog.check()


@pytest.mark.parametrize('pending,idle', [(True, 1), (False, 1)])
def test_other_teams_counting_down_cannot_hide_pending_or_unfilled_slot(pending, idle):
    clock = [0.]
    dog = ProgressWatchdog(300, lambda: clock[0])
    for now in range(0, 301, 10):
        clock[0] = now
        dog.observe(rows(idle, 8000-now), 4, 4, pending)
    with pytest.raises(RescueDue):
        dog.check()


def test_frozen_countdowns_trigger_rescue_despite_successful_screenshots():
    clock = [0.]
    dog = ProgressWatchdog(300, lambda: clock[0])
    for now in range(0, 301, 30):
        clock[0] = now
        dog.observe(rows(), 4, 4, None)
    with pytest.raises(RescueDue):
        dog.check()


@pytest.mark.parametrize('bad', [rows()[:-1], [QueueRow(1, 'unknown')]+rows()[1:],
    [QueueRow(1, 'mining')]+rows()[1:], [QueueRow(2, 'idle')]+rows()[1:]])
def test_incomplete_or_duplicate_team_read_never_authorizes_resume(bad):
    assert not complete_rows(bad, 4)


def rescue_bot(raw=None):
    bot, game = helpers.GatherTests().make_bot()
    raw = raw or SimpleNamespace(rescue_focus=Mock())
    bot.capacity = 4
    bot.ensure_world = Mock(return_value=True)
    bot.stable_queues = Mock(return_value=rows(idle=4))
    bot.logger = Mock()
    bot.stop_event = threading.Event()
    return bot, raw


def test_overnight_four_idle_queues_release_pending_without_counting_or_clicking_depart():
    bot, raw = rescue_bot()
    bot._departure_pending = (1, {1})
    bot.completed = 7
    control = Mock()
    result = RescueController(raw, control=control).recover(bot)
    assert result == rows(idle=4) and bot._departure_pending is None and bot.completed == 7
    raw.rescue_focus.assert_called_once()
    bot.ensure_world.assert_called_once_with(reset=True)
    bot.stable_queues.assert_called_once()
    control.restart.assert_not_called()


def test_completed_departure_is_counted_once_after_rescue_and_never_replayed():
    bot, raw = rescue_bot()
    bot._departure_pending = (1, {1})
    bot.completed = 7
    bot.stable_queues.return_value = rows()
    controller = RescueController(raw, control=Mock())
    controller.recover(bot)
    controller.recover(bot)
    assert bot._departure_pending is None and bot.completed == 8
    controller.control.restart.assert_not_called()


def test_failed_page_rescue_escalates_and_waits_for_world_and_full_queue_read():
    bot, raw = rescue_bot()
    bot._departure_pending = (1, {1})
    bot.ensure_world.side_effect = WindowUnavailable('background')
    control = Mock()
    with patch('connection_recovery.ConnectionRecovery') as recovery:
        bot.connection_recovery = None
        recovery.return_value.recover.return_value = rows(idle=4)
        result = RescueController(raw, clock=lambda: 300, control=control).recover(bot)
    control.restart.assert_called_once_with(bot)
    recovery.return_value.recover.assert_called_once_with(bot, deadline=480, take_focus=True)
    assert result == rows(idle=4) and bot._departure_pending is None


def test_unknown_rows_cannot_be_called_a_successful_page_rescue():
    bot, raw = rescue_bot()
    bot.stable_queues.return_value = [QueueRow(i,'unknown') for i in range(1,5)]
    control = Mock()
    control.restart.side_effect = WindowUnavailable('proxy not yet cleaned up')
    with pytest.raises(WindowUnavailable, match='proxy'):
        RescueController(raw, control=control).recover(bot)
    control.restart.assert_called_once()


def test_model_verification_failure_cannot_take_focus_or_restart():
    bot, raw = rescue_bot()
    bot.require_ready = Mock(side_effect=WorkflowError('changed model'))
    control = Mock()
    with pytest.raises(WorkflowError, match='changed model'):
        RescueController(raw, control=control).recover(bot)
    raw.rescue_focus.assert_not_called()
    control.restart.assert_not_called()


def test_f8_has_priority_even_when_watchdog_is_due():
    bot, raw = rescue_bot()
    dog = bot.rescue_watchdog = ProgressWatchdog(300, lambda: 900)
    dog.last_progress = 0
    bot.stop_event.set()
    control = Mock()
    with pytest.raises(RunStopped):
        RescueController(raw, control=control).recover(bot)
    raw.rescue_focus.assert_not_called()
    control.restart.assert_not_called()


def test_f8_during_restart_cooldown_never_closes_the_game():
    bot, raw = rescue_bot()
    raw.rescue_focus.side_effect = WindowUnavailable('background')
    control = Mock()
    controller = RescueController(raw, clock=lambda: 400, control=control)
    controller.last_restart = 300
    def sleep(seconds):
        assert seconds == 500
        bot.stop_event.set()
        bot.check_stop()
    bot.sleep = sleep
    with pytest.raises(RunStopped):
        controller.recover(bot)
    control.restart.assert_not_called()


def test_foreground_loss_keeps_task_alive_until_exact_rescue_deadline():
    bot, game = helpers.GatherTests().make_bot()
    bot.config['rescue_timeout_seconds'] = 300
    bot.run_continuous = Mock(side_effect=WindowUnavailable('background'))
    bot.connection_recovery = None
    clock = [0.]
    def sleep(seconds): clock[0] += seconds
    controller = Mock()
    def recover(owner):
        assert owner is bot and clock[0] == 300
        raise RunStopped('simulation done')
    controller.recover.side_effect = recover
    with patch('time.monotonic', side_effect=lambda: clock[0]), \
         patch('time.sleep', side_effect=sleep), \
         patch('rescue_watchdog.RescueController', return_value=controller), \
         pytest.raises(RunStopped, match='simulation done'):
        bot.run_resilient()
    assert bot.run_continuous.call_count == 150


def test_pending_is_recorded_before_post_click_settle_can_raise():
    bot, game = helpers.GatherTests().make_bot()
    bot.open_world_search = Mock(return_value=(1, {1}))
    def press(name):
        if name == 'depart':
            assert bot._departure_pending == (1, {1})
            raise WindowUnavailable('lost focus after mouse-up')
    bot.press = press
    bot.sleep = Mock()
    with pytest.raises(WindowUnavailable):
        bot.dispatch_one(continuous=True)
    assert bot._departure_pending == (1, {1}) and bot.completed == 0


def test_single_cycle_cannot_repeat_depart_after_foreground_loss():
    bot, game = helpers.GatherTests().make_bot()
    bot.connection_recovery = None
    bot._departure_pending = (1, {1})
    bot.confirm_departure = Mock(side_effect=RunStopped('still pending'))
    bot.run = Mock(side_effect=AssertionError('would duplicate departure'))
    with pytest.raises(RunStopped, match='still pending'):
        bot.run_resilient(continuous=False)
    bot.run.assert_not_called()


def test_run_session_can_wait_without_game_at_start_but_preview_stays_strict():
    with patch('crystal_gather.find_window', side_effect=WindowUnavailable('absent')):
        session = GameSession('Steam Top Heroes', defer_ready=True)
        assert session.hwnd is None
        with pytest.raises(WindowUnavailable):
            session.capture()
        with pytest.raises(WindowUnavailable):
            GameSession('Steam Top Heroes')


def test_long_mining_chat_returns_to_verify_hidden_timers_before_rescue_deadline():
    from test_chat_alarm import ChatAlarmTests
    watcher, queues, clock, _ = ChatAlarmTests().watcher()
    queues = rows(seconds=7200)
    dog = watcher.bot.rescue_watchdog = ProgressWatchdog(300, lambda: clock[0])
    watcher.bot.check_stop.side_effect = dog.check
    watcher.poll = Mock()
    with patch('chat_invites.time.monotonic', side_effect=lambda: clock[0]):
        assert watcher.wait(queues, observed_at=100) == 'queue_recheck'
    assert 120 <= clock[0]-100 < 121
    watcher.close_if_open.assert_called_once()
    assert dog.last_progress == 100  # Chat frames do not falsely reset progress.


def test_full_dispatch_loop_rescues_unacknowledged_departure_after_five_minutes():
    bot, game = helpers.GatherTests().make_bot(idle=1, acknowledge=False)
    bot.config.update(rescue_timeout_seconds=300, dispatches=4, queue_capacity=4)
    bot.capacity = 4
    bot.timeout, bot.poll = 2, 1
    bot.stop_event = threading.Event()
    bot.logger = Mock()
    clock = [0.]
    focused_at = []
    def focus():
        focused_at.append(clock[0])
        game.acknowledge = True
    game.rescue_focus = focus
    bot.queue_reader = Mock()
    bot.queue_reader.read.side_effect = lambda frame: rows(game.idle, int(8000-clock[0]))
    # The pixel simulator has only the city->world asset, so model a verified
    # return to city and retain the real fresh-frame world navigation.
    original_world = bot.ensure_world
    def ensure_world(reset=False):
        if reset: game.state = 'city'
        return original_world()
    bot.ensure_world = ensure_world
    def sleep(seconds):
        clock[0] += seconds
        bot.check_stop()
        assert clock[0] < 340, f'no progress: {bot.completed}, {game.idle}, {focused_at}, {bot.logger.call_args_list[-5:]}'
    bot.sleep = sleep
    def event(kind, data):
        if kind == 'monitor' and data.get('completed') == 1 and data.get('delay',0) > 0:
            bot.stop_event.set()
    bot.on_event = event
    with patch('time.monotonic', side_effect=lambda: clock[0]), \
         patch('rescue_game.GameControl') as native, pytest.raises(RunStopped):
        bot.run_resilient()
    assert len(focused_at) == 1 and 300 <= focused_at[0] < 305
    assert bot.completed == 1 and game.idle == 0 and bot._departure_pending is None
    assert game.clicked.count('depart') == 2  # One rejected, one freshly authorized.
    native.assert_not_called()
