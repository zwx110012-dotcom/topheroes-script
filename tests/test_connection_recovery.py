from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np
import pytest

from connection_recovery import ConnectionRecovery, DisconnectDialog, Disconnected, GuardedSession
from crystal_gather import CrystalGatherer, GameSession, QueueUnavailable, RunStopped
from queue_monitor import QueueRow
from top_heroes import WindowUnavailable, WindowChanged, WorkflowError, load_template
from visual_navigation import Page
import test_crystal_gather as helpers

BASE = Path(__file__).resolve().parents[1]
FIXTURES = sorted((BASE/'tests/fixtures/connection').glob('*.png'))


@pytest.mark.parametrize('image', FIXTURES)
def test_real_disconnect_dialog_locates_only_confirm(image):
    match = DisconnectDialog(BASE).match(load_template(image))
    assert match.score > .99 and match.center == (800, 537)


def test_generic_confirmation_or_incomplete_message_cannot_authorize_restart():
    frame = load_template(FIXTURES[0])
    dialog = DisconnectDialog(BASE)
    without_message = frame.copy()
    without_message[430:459, 670:929] = 114
    assert dialog.match(without_message) is None
    without_button = frame.copy()
    without_button[505:571, 710:891] = 114
    assert dialog.match(without_button) is None
    assert dialog.match(np.full((900,1600,3),114,np.uint8)) is None


def test_dialog_location_comes_from_pixels_not_fixed_screen_click():
    frame = load_template(FIXTURES[0])
    shifted = cv2.warpAffine(frame, np.float32([[1,0,12],[0,1,15]]), (1600,900),
                            borderValue=(114,114,114))
    assert DisconnectDialog(BASE).match(shifted).center == (812,552)


def test_guard_unwinds_workflow_before_background_queue_or_buttons_are_used():
    raw = Mock()
    raw.capture.return_value = load_template(FIXTURES[0])
    guarded = GuardedSession(raw, DisconnectDialog(BASE))
    with pytest.raises(Disconnected):
        guarded.capture()
    raw.click.assert_not_called()


def test_missing_native_window_polls_steam_recovery_and_f8_still_interrupts():
    raw,bot,nav,clock,_ = runtime(stop_at=3,unavailable_until=100)
    raw.allow_steam_restart = True
    with patch('connection_recovery.time.monotonic',side_effect=lambda:clock[0]), \
         patch('visual_navigation.VisualNavigator',return_value=nav), \
         patch('steam_restart.SteamRestartGuard') as guard, pytest.raises(RunStopped):
        ConnectionRecovery(BASE,raw).recover(bot)
    assert guard.call_count==1
    assert guard.return_value.poll.call_count==3
    bot.stable_queues.assert_not_called()


def runtime(persistent=False, unavailable_until=0, generation_change=False, click_moves=False,
            stop_at=100, queue_errors=0):
    clock = [0.]
    frame_popup = load_template(FIXTURES[0])
    frame_loading = np.zeros((900,1600,3),np.uint8)
    frame_world = frame_loading.copy(); frame_world[0,0,0] = 7
    class Raw:
        generation = 0
        captures = 0
        clicked_at = None
        def __init__(self): self.clicks = []
        def rebind(self): pass
        def capture(self):
            self.captures += 1
            if clock[0] < unavailable_until:
                raise WindowUnavailable('restarting')
            if generation_change and self.captures >= 2:
                self.generation = 1
            if persistent or self.clicked_at is None:
                return frame_popup
            return frame_world if clock[0]-self.clicked_at >= 6 else frame_loading
        def click(self, point):
            self.clicks.append((clock[0],self.captures,point))
            if click_moves and len(self.clicks)==1:
                raise WindowChanged('moved before click')
            self.clicked_at = clock[0]
    raw = Raw()
    def check_stop():
        if clock[0] >= stop_at:
            raise RunStopped('F8')
    def sleep(seconds):
        clock[0] += seconds
        check_stop()
    rows = [QueueRow(i,'mining',30+i) for i in range(1,4)]
    bot = SimpleNamespace(capacity=3, check_stop=check_stop, sleep=sleep, log=Mock(), require_ready=Mock(),
                          ensure_world=Mock(), stable_queues=Mock(side_effect=
                          [QueueUnavailable('still loading')]*queue_errors+[rows]))
    nav = Mock()
    nav.observe.side_effect = lambda f: Page('世界地图') if f[0,0,0]==7 else Page('未知画面')
    return raw,bot,nav,clock,rows


def test_retries_two_fresh_frames_then_waits_for_loading_and_complete_queues():
    raw,bot,nav,clock,rows = runtime(queue_errors=1)
    with patch('connection_recovery.time.monotonic',side_effect=lambda:clock[0]), \
         patch('visual_navigation.VisualNavigator',return_value=nav):
        result = ConnectionRecovery(BASE,raw).recover(bot)
    assert result == rows and bot.stable_queues.call_count == 2
    assert len(raw.clicks)==1 and raw.clicks[0][1]>=2 and raw.clicks[0][2]==(800,537)
    assert clock[0]-raw.clicked_at >= 6


def test_persistent_disconnect_retries_at_most_every_thirty_seconds_until_f8():
    raw,bot,nav,clock,_ = runtime(persistent=True,stop_at=65)
    with patch('connection_recovery.time.monotonic',side_effect=lambda:clock[0]), \
         patch('visual_navigation.VisualNavigator',return_value=nav), pytest.raises(RunStopped):
        ConnectionRecovery(BASE,raw).recover(bot)
    times = [call[0] for call in raw.clicks]
    assert len(times)==3 and all(b-a>=30 for a,b in zip(times,times[1:]))
    bot.stable_queues.assert_not_called()


@pytest.mark.parametrize('stop_at,unavailable',[(.5,0),(4,0),(3,100)])
def test_f8_interrupts_before_click_during_loading_or_while_window_missing(stop_at,unavailable):
    raw,bot,nav,clock,_ = runtime(stop_at=stop_at,unavailable_until=unavailable)
    with patch('connection_recovery.time.monotonic',side_effect=lambda:clock[0]), \
         patch('visual_navigation.VisualNavigator',return_value=nav), pytest.raises(RunStopped):
        ConnectionRecovery(BASE,raw).recover(bot)
    assert len(raw.clicks)==(1 if stop_at==4 else 0)


@pytest.mark.parametrize('generation_change,click_moves',[(True,False),(False,True)])
def test_recreated_or_moved_window_discards_old_button_confirmation(generation_change,click_moves):
    raw,bot,nav,clock,_ = runtime(generation_change=generation_change,click_moves=click_moves)
    with patch('connection_recovery.time.monotonic',side_effect=lambda:clock[0]), \
         patch('visual_navigation.VisualNavigator',return_value=nav):
        ConnectionRecovery(BASE,raw).recover(bot)
    assert raw.clicks[-1][1] >= (3 if generation_change else 4)


def test_model_validation_failure_is_fatal_and_cannot_click():
    raw,bot,nav,clock,_ = runtime()
    bot.require_ready.side_effect = WorkflowError('model changed')
    with patch('connection_recovery.time.monotonic',side_effect=lambda:clock[0]), \
         patch('visual_navigation.VisualNavigator',return_value=nav), pytest.raises(WorkflowError,match='model changed'):
        ConnectionRecovery(BASE,raw).recover(bot)
    assert raw.clicks == []


def test_unknown_or_missing_countdown_after_restart_keeps_waiting():
    raw,bot,nav,clock,rows = runtime()
    incomplete = [QueueRow(1,'mining'), QueueRow(2,'unknown'), QueueRow(3,'idle')]
    bot.stable_queues.side_effect = [incomplete, rows]
    with patch('connection_recovery.time.monotonic',side_effect=lambda:clock[0]), \
         patch('visual_navigation.VisualNavigator',return_value=nav):
        assert ConnectionRecovery(BASE,raw).recover(bot)==rows
    assert bot.stable_queues.call_count==2 and len(raw.clicks)==1


def test_rebind_changes_generation_and_discards_old_capture_box():
    session = GameSession.__new__(GameSession)
    session.title='Steam Top Heroes'; session.hwnd=10; session.generation=4; session.capture_box=(1,2,3,4)
    with patch('crystal_gather.find_window',return_value=20):
        session.rebind()
    assert session.hwnd==20 and session.generation==5 and session.capture_box is None


def test_reconnected_pending_departure_is_counted_once_or_refreshed_when_not_sent():
    bot,_ = helpers.GatherTests().make_bot()
    bot._departure_pending=(1,{1});bot.completed=2
    rows=[QueueRow(1,'mining',30),QueueRow(2,'idle'),QueueRow(3,'mining',20)]
    assert bot.reconcile_reconnected_departure(rows)
    assert bot.completed==3 and bot._departure_pending is None
    bot._departure_pending=(1,{2})
    assert bot.reconcile_reconnected_departure(rows)
    assert bot.completed==3 and bot._departure_pending is None
    bot._departure_pending=(1,{1})
    rows[0]=QueueRow(1,'unknown')
    assert not bot.reconcile_reconnected_departure(rows)
    assert bot.completed==3 and bot._departure_pending is not None


@pytest.mark.parametrize('when',['before_depart','after_depart'])
@pytest.mark.parametrize('continuous',[False,True])
def test_complete_cycle_survives_restart_without_duplicate_departure(when,continuous):
    import threading
    bot,game = helpers.GatherTests().make_bot(idle=3)
    bot.config['dispatches']=2
    bot.stop_event=threading.Event()
    def event(kind,data):
        if continuous and kind=='monitor' and data.get('completed')==2 and data.get('delay',0)>0:
            bot.stop_event.set()
    bot.on_event=event
    clock=[0.]
    popup=load_template(FIXTURES[0])
    original_capture,original_click=game.capture,game.click
    state={'injected':False,'popup':False}
    def capture():
        if not state['injected'] and when=='before_depart' and game.state=='march':
            state.update(injected=True,popup=True)
        if state['popup']: return popup.copy()
        canvas=np.zeros((900,1600,3),np.uint8)
        canvas[:400,:600]=original_capture()
        return canvas
    def click(point):
        if state['popup']:
            assert point==(800,537)
            state['popup']=False;game.state='city'
            return
        previous=game.state
        original_click(point)
        if not state['injected'] and when=='after_depart' and previous=='march':
            state.update(injected=True,popup=True)
    game.capture,game.click=capture,click
    bot.queue_reader=Mock()
    bot.queue_reader.read.side_effect=lambda f:[QueueRow(i,'idle') if i<=game.idle else
                                               QueueRow(i,'mining',30+i) for i in range(1,4)]
    def sleep(seconds):
        clock[0]+=seconds
        assert clock[0]<150, 'recovery did not terminate'
        bot.check_stop()
    bot.sleep=sleep
    with patch('connection_recovery.time.monotonic',side_effect=lambda:clock[0]):
        if continuous:
            with pytest.raises(RunStopped):bot.run_resilient(continuous=True)
        else:
            bot.run_resilient(continuous=False)
    assert state['injected'] and not state['popup']
    assert bot.completed==2 and game.clicked.count('depart')==2 and game.idle==1
