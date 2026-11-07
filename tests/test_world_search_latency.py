"""Search opens promptly while retaining fresh queues and click confirmation."""
from unittest.mock import Mock, patch

import pytest

from crystal_gather import NoIdleQueues, QueueUnavailable, RunStopped
from queue_monitor import QueueRow
import test_crystal_gather as helpers


def test_search_icon_has_one_two_frame_wait_instead_of_two():
    bot, game = helpers.GatherTests().make_bot(idle=1)
    game.state = 'world'
    with patch('crystal_gather.time.sleep'), patch.object(bot, 'wait', wraps=bot.wait) as wait:
        count, rows = bot.open_world_search()
    assert count == 1 and rows is None
    assert [call.args[0] for call in wait.call_args_list].count('search_icon') == 1
    assert game.clicked == ['search_icon', 'resources', 'red_crystal']


def test_continuous_mode_uses_latest_full_queue_check_after_world_entry():
    bot, game = helpers.GatherTests().make_bot(idle=1)
    observations = []
    rows = [QueueRow(1, 'idle'), QueueRow(2, 'returning', 10), QueueRow(3, 'mining', 30)]
    def read():
        observations.append(game.state)
        return rows
    bot.stable_queues = Mock(side_effect=read)
    bot.stable_idle_count = Mock(side_effect=AssertionError('duplicate idle-only scan'))
    with patch('crystal_gather.time.sleep'):
        count, idle_rows = bot.open_world_search(continuous=True)
    assert observations == ['world']
    assert count == 1 and idle_rows == {1, 2}
    assert game.clicked == ['world', 'search_icon', 'resources', 'red_crystal']


@pytest.mark.parametrize('outcome', [QueueUnavailable('occluded'),
                         [QueueRow(i, 'mining', 30) for i in range(1, 4)]])
def test_fresh_queue_failure_or_no_idle_never_opens_search(outcome):
    bot, game = helpers.GatherTests().make_bot()
    game.state = 'world'
    bot.stable_queues = Mock(side_effect=outcome if isinstance(outcome, Exception) else None,
                             return_value=outcome)
    with pytest.raises((QueueUnavailable, NoIdleQueues)):
        bot.open_world_search(continuous=True)
    assert game.clicked == []


def test_queue_check_does_not_replace_fresh_search_button_observations():
    bot, game = helpers.GatherTests().make_bot(idle=1)
    game.state = 'world'
    original = game.capture
    frames = []
    def capture():
        frames.append(game.state)
        return original()
    game.capture = capture
    bot.stable_queues = Mock(return_value=[QueueRow(1, 'idle'), QueueRow(2, 'mining', 30),
                                          QueueRow(3, 'mining', 40)])
    with patch('crystal_gather.time.sleep'):
        bot.open_world_search(continuous=True)
    assert frames.count('world') >= 3  # initial page check plus two fresh click frames
    assert game.clicked.count('search_icon') == 1


def test_continuous_start_uses_loop_world_check_without_startup_waits():
    import threading
    bot, game = helpers.GatherTests().make_bot()
    game.state = 'world'
    bot.stop_event = threading.Event()
    bot.matches = Mock(return_value=[object()])
    bot.wait = Mock(side_effect=AssertionError('redundant startup wait'))
    bot.stable_queues = Mock(return_value=[QueueRow(i, 'mining', 30) for i in range(1,4)])
    bot.on_event = lambda kind, data: bot.stop_event.set() if kind == 'monitor' else None
    with pytest.raises(RunStopped):
        bot.run_continuous()
    bot.matches.assert_called_once_with('search_icon')
    assert game.clicked == []


def test_continuous_dispatch_from_city_still_confirms_newly_busy_row():
    bot, game = helpers.GatherTests().make_bot(idle=1)
    bot.queue_reader = Mock()
    def read(_frame):
        return [QueueRow(1, 'idle') if game.idle else QueueRow(1, 'mining', 30),
                QueueRow(2, 'mining', 40), QueueRow(3, 'mining', 50)]
    bot.queue_reader.read.side_effect = read
    with patch('crystal_gather.time.sleep'):
        bot.dispatch_one(continuous=True)
    assert game.clicked.count('search_icon') == 1
    assert game.clicked.count('depart') == 1
    assert game.idle == 0 and bot.completed == 1


def test_search_retry_uses_new_queue_rows_for_departure_confirmation():
    import numpy as np
    from crystal_gather import TemplateTimeout
    bot, game = helpers.GatherTests().make_bot(idle=1)
    bot.open_world_search = Mock(side_effect=[(1, {1}), (1, {2})])
    seen = []
    def press(name):
        seen.append(name)
        if name == 'gather' and seen.count('gather') == 1:
            raise TemplateTimeout('gather', np.zeros((10,10,3),np.uint8), 'not ready')
    bot.press = press
    bot.search_retry_allowed = Mock(return_value=True)
    bot.confirm_departure = Mock()
    with patch('crystal_gather.time.sleep'):
        bot.dispatch_one(continuous=True)
    assert bot.open_world_search.call_count == 2
    bot.confirm_departure.assert_called_once_with(1, {2})
    assert seen.count('depart') == 1
