"""Exercise process identity, cleanup ordering and cancellation without OS input."""
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from crystal_gather import RunStopped
from rescue_game import GameControl, VerifiedGame, unique_game
from top_heroes import WindowUnavailable, WorkflowError


def setup_restart(running=True, graceful=True):
    clock = [0.]
    def sleep(seconds): clock[0] += seconds
    bot = SimpleNamespace(check_stop=Mock(), sleep=sleep, log=Mock())
    game = Mock()
    game.hwnd, game.handle = 17, 123
    game.running.side_effect = [True, False, False] if graceful else lambda: True
    game.__enter__ = Mock(return_value=game)
    game.__exit__ = Mock()
    return bot, game, clock


def test_restart_waits_for_game_exit_and_proxy_cleanup_before_steam_request():
    bot, game, clock = setup_restart()
    events = []
    def proxy(_name):
        events.append(('proxy', clock[0]))
        return clock[0] < 8
    with patch('rescue_game.unique_game', return_value=game), \
         patch('rescue_game.game_is_running', side_effect=[True, False, False, False, False]), \
         patch('rescue_game.image_is_running', side_effect=proxy), \
         patch('rescue_game.win32gui.PostMessage') as close, \
         patch('rescue_game.request_launch', side_effect=lambda:events.append(('launch',clock[0])) or True), \
         patch('rescue_game.time.monotonic', side_effect=lambda:clock[0]):
        GameControl().restart(bot)
    close.assert_called_once_with(17, 16, 0, 0)
    game.kernel.TerminateProcess.assert_not_called()
    assert events[-1] == ('launch', 8.5)


def test_absent_game_still_waits_for_proxy_instead_of_starting_duplicate():
    bot, game, clock = setup_restart()
    with patch('rescue_game.unique_game') as select, \
         patch('rescue_game.game_is_running', return_value=False), \
         patch('rescue_game.image_is_running', return_value=True), \
         patch('rescue_game.request_launch') as launch, \
         patch('rescue_game.time.monotonic', side_effect=lambda:clock[0]), \
         pytest.raises(WindowUnavailable, match='专用代理'):
        GameControl().restart(bot)
    assert clock[0] == 60
    select.assert_not_called()
    launch.assert_not_called()


def test_hung_game_only_terminates_original_verified_handle_after_twenty_seconds():
    bot, game, clock = setup_restart(graceful=False)
    with patch('rescue_game.unique_game', return_value=game), \
         patch('rescue_game.game_is_running', side_effect=[True, False]), \
         patch('rescue_game.image_is_running', return_value=False), \
         patch('rescue_game.win32gui.PostMessage'), \
         patch('rescue_game.request_launch', return_value=True) as launch, \
         patch('rescue_game.time.monotonic', side_effect=lambda:clock[0]):
        GameControl().restart(bot)
    assert clock[0] == 25
    game.kernel.TerminateProcess.assert_called_once_with(123, 1)
    launch.assert_called_once()


@pytest.mark.parametrize('phase', ['graceful_wait', 'proxy_wait'])
def test_f8_during_shutdown_or_cleanup_cancels_termination_and_launch(phase):
    bot, game, clock = setup_restart(graceful=False)
    def sleep(seconds):
        clock[0] += seconds
        raise RunStopped('F8')
    bot.sleep = sleep
    with patch('rescue_game.unique_game', return_value=game), \
         patch('rescue_game.game_is_running', return_value=phase=='graceful_wait'), \
         patch('rescue_game.image_is_running', return_value=True), \
         patch('rescue_game.win32gui.PostMessage'), \
         patch('rescue_game.request_launch') as launch, \
         patch('rescue_game.time.monotonic', side_effect=lambda:clock[0]), \
         pytest.raises(RunStopped):
        GameControl().restart(bot)
    game.kernel.TerminateProcess.assert_not_called()
    launch.assert_not_called()


def test_multiple_game_windows_never_authorize_focus_or_shutdown():
    with patch('rescue_game.game_windows', return_value=[(1,'Steam Top Heroes'),(2,'Steam Top Heroes')]), \
         patch('rescue_game.VerifiedGame') as verified, \
         pytest.raises(WorkflowError, match='不接管'):
        unique_game()
    verified.assert_not_called()


def test_same_title_wrong_executable_is_rejected_and_process_handle_closed():
    kernel = Mock()
    kernel.OpenProcess.return_value = 123
    def query(handle, flags, buffer, size):
        buffer.value = r'C:\unrelated\TopHeroes_Steam.exe'
        return True
    kernel.QueryFullProcessImageNameW.side_effect = query
    with patch('rescue_game.ctypes.WinDLL', return_value=kernel), \
         patch('rescue_game.win32process.GetWindowThreadProcessId', return_value=(50,60)), \
         patch('rescue_game.installed_game_paths', return_value={r'd:\steam\topheroes_steam.exe'}), \
         pytest.raises(WorkflowError, match='拒绝接管'):
        VerifiedGame(17)
    kernel.CloseHandle.assert_called_once_with(123)
    kernel.TerminateProcess.assert_not_called()


def test_foreground_denial_does_not_allow_capture_or_click_and_detaches_threads():
    game = VerifiedGame.__new__(VerifiedGame)
    game.hwnd = 17
    game.kernel = Mock()
    game.kernel.GetCurrentThreadId.return_value = 70
    game.require_window = Mock()
    with patch('rescue_game.win32gui.GetForegroundWindow', return_value=18), \
         patch('rescue_game.win32gui.ShowWindow'), \
         patch('rescue_game.win32gui.PeekMessage') as peek, \
         patch('rescue_game.win32gui.SetForegroundWindow'), \
         patch('rescue_game.win32process.GetWindowThreadProcessId', return_value=(50,60)), \
         patch('rescue_game.win32process.AttachThreadInput') as attach, \
         pytest.raises(WindowUnavailable, match='Windows'):
        game.focus(lambda:None)
    assert [call.args for call in attach.call_args_list] == [(70,50,True),(70,50,False)]
    peek.assert_called_once_with(None,0,0,0)
