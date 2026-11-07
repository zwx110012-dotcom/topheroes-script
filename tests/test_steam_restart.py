from unittest.mock import Mock, patch

import pytest

from crystal_gather import RunStopped
from steam_restart import Dialog, SteamDialogs, SteamRestartGuard, matches_error


@pytest.mark.parametrize('text',[
    ('Top Heroes','游戏已在运行','关闭'),
    ('王国之歌','启动此游戏时发生错误：游戏已在运行','关闭'),
    ('Steam Top Heroes','Game already running','Close'),
])
def test_recognizes_only_the_requested_game_error(text):
    assert matches_error(text)


@pytest.mark.parametrize('text',[
    ('Other Game','游戏已在运行','关闭'),
    ('Top Heroes','磁盘空间不足','关闭'),
    ('Top Heroes','开始游戏','关闭'),
    ('Steam','游戏已在运行','关闭'),
])
def test_unrelated_errors_never_authorize_close(text):
    assert not matches_error(text)


def setup_guard():
    now=[0.]
    dialog=Dialog(123,(1,2),('Top Heroes','游戏已在运行','关闭'))
    backend=Mock()
    backend.find.return_value=dialog
    backend.close.return_value=True
    running=Mock(return_value=False)
    launch=Mock(return_value=True)
    guard=SteamRestartGuard(Mock(),backend,running,launch,clock=lambda:now[0])
    return guard,backend,running,launch,now,dialog


def test_close_then_wait_for_dialog_disappearance_and_retry_through_steam():
    guard,backend,running,launch,now,_=setup_guard()
    guard.poll()
    backend.close.assert_called_once()
    launch.assert_not_called()
    backend.find.return_value=None
    now[0]=4;guard.poll();launch.assert_not_called()
    now[0]=6;guard.poll();launch.assert_called_once()
    assert not guard.pending


def test_waits_for_old_process_exit_instead_of_launching_twice():
    guard,backend,running,launch,now,_=setup_guard()
    guard.poll();backend.find.return_value=None;running.return_value=True
    now[0]=6;guard.poll();launch.assert_not_called()
    running.return_value=False;now[0]=8;guard.poll();launch.assert_called_once()


def test_live_game_cancels_duplicate_restart_after_wait():
    guard,backend,running,launch,now,_=setup_guard()
    guard.poll();backend.find.return_value=None;running.return_value=True
    now[0]=32;guard.poll()
    assert not guard.pending
    launch.assert_not_called()


def test_persistent_dialog_never_causes_continuous_close_or_launch():
    guard,backend,_,launch,now,_=setup_guard()
    for second in range(30):
        now[0]=second;guard.poll()
    backend.close.assert_called_once()
    launch.assert_not_called()


def test_failed_or_changed_close_target_never_triggers_launch():
    guard,backend,_,launch,now,_=setup_guard()
    backend.close.return_value=False
    guard.poll();backend.find.return_value=None;now[0]=8;guard.poll()
    assert not guard.pending
    launch.assert_not_called()


def test_retries_are_spaced_and_capped_at_three():
    guard,backend,_,launch,now,dialog=setup_guard()
    for start in (0,36,72,108):
        backend.find.return_value=dialog;now[0]=start;guard.poll()
        backend.find.return_value=None;now[0]=start+6;guard.poll()
    assert launch.call_count==3 and guard.attempts==3


def test_stop_before_close_prevents_all_input():
    guard,backend,_,launch,_,_=setup_guard()
    with pytest.raises(RunStopped):
        guard.poll(Mock(side_effect=RunStopped('F8')))
    backend.close.assert_not_called();launch.assert_not_called()


def test_stop_after_close_prevents_the_delayed_restart():
    guard,backend,_,launch,now,_=setup_guard()
    guard.poll();backend.find.return_value=None;now[0]=6
    with pytest.raises(RunStopped):
        guard.poll(Mock(side_effect=RunStopped('F8')))
    launch.assert_not_called()


def test_provider_failure_waits_without_blind_click_or_launch():
    guard,backend,_,launch,_,_=setup_guard()
    backend.find.side_effect=RuntimeError('provider unavailable')
    guard.poll()
    assert guard.disabled and not guard.pending
    backend.close.assert_not_called();launch.assert_not_called()


def test_close_rereads_exact_button_identity_before_invocation():
    backend=SteamDialogs.__new__(SteamDialogs)
    old=Dialog(123,(1,2),('Top Heroes','游戏已在运行','关闭'))
    new=Dialog(123,(1,3),old.text,Mock())
    backend._windows=Mock(return_value=[123]);backend._read=Mock(return_value=new)
    assert backend.close(old) is False
    new.button.GetCurrentPattern.assert_not_called()


def test_no_error_never_launches_game_while_waiting_for_foreground():
    guard,backend,_,launch,now,_=setup_guard()
    backend.find.return_value=None
    for second in (0,30,90):
        now[0]=second;guard.poll()
    backend.close.assert_not_called();launch.assert_not_called()
