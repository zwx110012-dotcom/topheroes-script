import queue
import threading
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

import game_launcher
from top_heroes import WorkflowError
from topheroes_gui import HelperApp


def test_existing_window_never_sends_another_steam_request():
    with patch('game_launcher.game_windows',return_value=[(1,'Steam Top Heroes')]), \
         patch('game_launcher.os.startfile') as launch, patch('game_launcher.subprocess.run') as tasklist:
        assert game_launcher.request_launch() is False
    launch.assert_not_called(); tasklist.assert_not_called()


def test_loading_process_without_window_is_also_treated_as_running():
    response=SimpleNamespace(returncode=0,stdout='"TopHeroes_Steam.exe","1234","Console","1","200 K"\n')
    with patch('game_launcher.game_windows',return_value=[]), \
         patch('game_launcher.subprocess.run',return_value=response), \
         patch('game_launcher.os.startfile') as launch:
        assert game_launcher.request_launch() is False
    launch.assert_not_called()


def test_new_launch_goes_through_steam_and_keeps_its_configured_options():
    response=SimpleNamespace(returncode=0,stdout='INFO: No tasks match the specified criteria.\n')
    with patch('game_launcher.game_windows',return_value=[]), \
         patch('game_launcher.subprocess.run',return_value=response), \
         patch('game_launcher.os.startfile') as launch:
        assert game_launcher.request_launch() is True
    launch.assert_called_once_with('steam://rungameid/4007740')


def test_missing_steam_reports_error_instead_of_starting_game_directly():
    with patch('game_launcher.game_is_running',return_value=False), \
         patch('game_launcher.os.startfile',side_effect=OSError('missing handler')), \
         pytest.raises(WorkflowError,match='Steam'):
        game_launcher.request_launch()


def test_uncertain_process_state_does_not_risk_duplicate_launch():
    with patch('game_launcher.game_windows',return_value=[]), \
         patch('game_launcher.subprocess.run',return_value=SimpleNamespace(returncode=1,stdout='')), \
         patch('game_launcher.os.startfile') as launch, pytest.raises(WorkflowError):
        game_launcher.request_launch()
    launch.assert_not_called()


def test_launch_button_ignores_repeat_click_and_never_starts_gathering():
    app=HelperApp.__new__(HelperApp)
    app.busy=False; app.launching_game=False; app.closing=threading.Event()
    app.launch_button=Mock();app.status_var=Mock();app.detail_var=Mock();app.add_log=Mock()
    app.events=queue.Queue();app.worker=None;app.start=Mock();app.refresh_windows=Mock();app.stop_event=threading.Event()
    workers=[]
    def thread(*,target,daemon):
        workers.append(target)
        return SimpleNamespace(start=lambda:None)
    with patch('topheroes_gui.threading.Thread',side_effect=thread):
        app.launch_game();app.launch_game()
    assert len(workers)==1 and app.launching_game
    with patch('game_launcher.request_launch',return_value=False), \
         patch('steam_restart.SteamRestartGuard') as guard:
        guard.return_value.pending=False
        workers[0]()
    kind,data=app.events.get_nowait()
    assert kind=='game_launch_done' and data[0]
    app.finish_game_launch(*data)
    assert not app.launching_game and not app.busy and app.worker is None
    app.start.assert_not_called()
