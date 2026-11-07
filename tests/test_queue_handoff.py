"""Continuous dispatch shares only the current, complete queue observation."""
import threading
import time
from unittest.mock import Mock, patch

import pytest

from crystal_gather import QueueUnavailable, RunStopped
from queue_monitor import QueueRow
import test_crystal_gather as helpers


def setup_handoff():
    bot, game = helpers.GatherTests().make_bot(idle=2)
    game.state = 'world'
    game.generation = 0
    rows = [QueueRow(1, 'mining', 90), QueueRow(2, 'idle'), QueueRow(3, 'idle')]
    bot._search_queue_snapshot = (rows, time.monotonic(), 0)
    bot.stable_queues = Mock(return_value=rows)
    return bot, game, rows


def test_immediate_handoff_removes_second_scan_but_keeps_two_fresh_click_frames():
    bot, game, _ = setup_handoff()
    with patch('crystal_gather.time.sleep'), patch.object(game, 'capture', wraps=game.capture) as capture:
        assert bot.open_world_search(continuous=True) == (2, {2, 3})
    bot.stable_queues.assert_not_called()
    assert capture.call_count >= 7  # page + two each for search/resources/crystal
    assert game.clicked == ['search_icon', 'resources', 'red_crystal']
    assert bot._search_queue_snapshot is None
    game.state = 'world'
    with patch('crystal_gather.time.sleep'):
        bot.open_world_search(continuous=True)
    bot.stable_queues.assert_called_once()  # never reused by another attempt


@pytest.mark.parametrize('invalid', ['expired', 'future', 'generation', 'during_capture',
                                    'missing', 'duplicate', 'unknown', 'timer', 'navigation'])
def test_invalid_observation_forces_fresh_scan_and_never_clicks_if_it_fails(invalid):
    bot, game, rows = setup_handoff()
    if invalid == 'expired':
        bot._search_queue_snapshot = (rows, time.monotonic() - 6, 0)
    elif invalid == 'future':
        bot._search_queue_snapshot = (rows, time.monotonic() + 6, 0)
    elif invalid == 'generation':
        game.generation = 1
    elif invalid == 'during_capture':
        original = game.capture
        def changed():
            game.generation += 1
            return original()
        game.capture = changed
    elif invalid == 'missing':
        rows.pop()
    elif invalid == 'duplicate':
        rows[2] = QueueRow(2, 'idle')
    elif invalid == 'unknown':
        rows[0] = QueueRow(1, 'unknown')
    elif invalid == 'timer':
        rows[0] = QueueRow(1, 'mining')
    elif invalid == 'navigation':
        game.state = 'city'
        bot.ensure_world = Mock(side_effect=lambda: setattr(game, 'state', 'world'))
    bot.stable_queues.side_effect = QueueUnavailable('fresh rows unavailable')
    with pytest.raises(QueueUnavailable):
        bot.open_world_search(continuous=True)
    bot.stable_queues.assert_called_once()
    assert game.clicked == []
    assert bot._search_queue_snapshot is None


def test_stop_between_handoff_and_search_prevents_all_clicks():
    bot, game, _ = setup_handoff()
    bot.stop_event = threading.Event()
    bot.stop_event.set()
    with pytest.raises(RunStopped):
        bot.open_world_search(continuous=True)
    assert bot._search_queue_snapshot is None
    assert game.clicked == []


@pytest.mark.parametrize('capacity', [3, 4])
def test_consecutive_pixel_dispatches_scan_each_queue_once_and_count_once(capacity):
    bot, game = helpers.GatherTests().make_bot(idle=capacity)
    bot.capacity = capacity
    bot.config.update(queue_capacity=capacity, dispatches=capacity)
    game.state = 'world'
    bot.stop_event = threading.Event()
    bot.config['search_wait_seconds'] = 0
    def read(frame):
        idle = bot.idle_count(frame)
        return [QueueRow(n, 'mining', 90) if n <= capacity-idle else QueueRow(n, 'idle')
                for n in range(1, capacity+1)]
    bot.queue_reader = Mock()
    bot.queue_reader.read.side_effect = read
    def event(kind, data):
        if kind == 'monitor' and bot.completed == capacity:
            bot.stop_event.set()
    bot.on_event = event
    with patch('crystal_gather.time.sleep'), patch.object(bot, 'stable_queues', wraps=bot.stable_queues) as scans:
        with pytest.raises(RunStopped):
            bot.run_continuous()
    assert scans.call_count == capacity+1  # dispatch decisions + final monitoring
    assert bot.completed == capacity and game.idle == 0
    assert game.clicked.count('depart') == capacity
    assert game.clicked.count('search_icon') == capacity
    assert bot._departure_pending is None and bot._search_queue_snapshot is None
