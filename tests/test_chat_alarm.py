"""The chat observer runs until an absolute mining alarm, without a 30s cap."""
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock,patch

import numpy as np

from chat_invites import ChatWatcher,Invite
from crystal_gather import CrystalGatherer,RunStopped
from queue_monitor import QueueRow
from top_heroes import Match


class ChatAlarmTests(unittest.TestCase):
    def watcher(self,now=100):
        clock=[float(now)]
        stop=threading.Event()
        bot=Mock();bot.capacity=4;bot.completed=0;bot.timeout=12
        bot.session.generation=0
        bot.session.capture.return_value=np.zeros((900,1600,3),np.uint8)
        def check_stop():
            if stop.is_set():raise RunStopped('stop')
        bot.check_stop.side_effect=check_stop
        def sleep(seconds):
            check_stop();clock[0]+=seconds;check_stop()
        bot.sleep.side_effect=sleep
        w=ChatWatcher.__new__(ChatWatcher)
        w.bot=bot;w.quota_dirty=False;w.alarm_deadline=None
        w.images=Mock();w.images.visible.return_value=True
        w.images.templates={};w.images.invitations.return_value=[]
        w.previous=None;w.attempted=set();w.joined=0
        w.enter=Mock();w.close_if_open=Mock()
        rows=[QueueRow(i,'mining',seconds) for i,seconds in enumerate((3600,120,1800,2400),1)]
        return w,rows,clock,stop

    def test_observes_continuously_past_thirty_seconds_until_shortest_timer(self):
        w,rows,clock,_=self.watcher()
        w.poll=Mock()
        with patch('chat_invites.time.monotonic',side_effect=lambda:clock[0]):
            self.assertEqual(w.wait(rows,observed_at=100),'alarm')
        self.assertAlmostEqual(clock[0],215)
        self.assertGreater(w.poll.call_count,300)
        w.close_if_open.assert_called_once()
        self.assertTrue(all(call.args[0]<=.3 for call in w.bot.sleep.call_args_list))

    def test_old_snapshot_and_time_opening_chat_do_not_extend_alarm(self):
        w,rows,clock,_=self.watcher(now=120)
        w.poll=Mock()
        w.enter.side_effect=lambda:clock.__setitem__(0,clock[0]+12)
        with patch('chat_invites.time.monotonic',side_effect=lambda:clock[0]):
            w.wait(rows,observed_at=100)
        self.assertAlmostEqual(clock[0],215)

    def test_expired_alarm_never_opens_chat(self):
        w,rows,clock,_=self.watcher(now=300)
        with patch('chat_invites.time.monotonic',side_effect=lambda:clock[0]):
            self.assertEqual(w.wait(rows,observed_at=100),'alarm')
        w.enter.assert_not_called();w.bot.session.click.assert_not_called()

    def test_missing_timer_or_returning_queue_prevents_chat(self):
        for row in (QueueRow(1,'mining'),QueueRow(1,'returning',15)):
            w,rows,_,_=self.watcher();rows[0]=row
            self.assertEqual(w.wait(rows),'not_ready')
            w.enter.assert_not_called()

    def test_alarm_interrupts_a_twelve_second_button_wait(self):
        w,rows,clock,_=self.watcher()
        rows[1]=QueueRow(2,'mining',9)
        w.images.matches.return_value=[]
        w.poll=lambda:w.click_asset('new_messages')
        with patch('chat_invites.time.monotonic',side_effect=lambda:clock[0]):
            self.assertEqual(w.wait(rows,observed_at=100),'alarm')
        self.assertAlmostEqual(clock[0],104)
        w.bot.session.click.assert_not_called();w.close_if_open.assert_called_once()

    def test_slow_inference_past_alarm_cannot_click_join(self):
        w,rows,clock,_=self.watcher()
        rows[1]=QueueRow(2,'mining',9)
        invite=Invite(Match(.99,700,400,100,40),'card',(650,250,950,450))
        w.previous=invite
        def inference(frame):clock[0]+=5;return [invite]
        w.images.invitations.side_effect=inference
        with patch('chat_invites.time.monotonic',side_effect=lambda:clock[0]):
            self.assertEqual(w.wait(rows,observed_at=100),'alarm')
        w.bot.session.click.assert_not_called()
        self.assertEqual(w.attempted,set())

    def test_f8_interrupts_chat_without_any_cleanup_click(self):
        w,rows,clock,stop=self.watcher()
        w.poll=Mock(side_effect=stop.set)
        with patch('chat_invites.time.monotonic',side_effect=lambda:clock[0]):
            with self.assertRaises(RunStopped):w.wait(rows,observed_at=100)
        w.bot.session.click.assert_not_called();w.close_if_open.assert_not_called()
        self.assertIsNone(w.alarm_deadline)

    def test_outer_loop_waits_for_return_refills_then_arms_new_shortest_timer(self):
        clock=[0.]
        bot=CrystalGatherer.__new__(CrystalGatherer)
        bot.config={'chat_invites':True,'dispatches':4};bot.capacity=4;bot.completed=0
        bot.base=Path('.');bot.session=Mock();bot.stop_event=threading.Event()
        bot.require_ready=Mock();bot.matches=Mock(return_value=[Mock()]);bot.wait=Mock()
        bot.log=Mock();bot.emit=Mock();bot.dispatch_one=Mock()
        def check_stop():
            if bot.stop_event.is_set():raise RunStopped('stop')
        bot.check_stop=check_stop
        def sleep(seconds):clock[0]+=seconds;check_stop()
        bot.sleep=sleep
        first=[QueueRow(i,'mining',seconds) for i,seconds in enumerate((120,900,1200,1800),1)]
        returning=[QueueRow(1,'returning',10),*first[1:]]
        idle=[QueueRow(1,'idle'),*first[1:]]
        second=[QueueRow(i,'mining',seconds) for i,seconds in enumerate((3600,300,600,900),1)]
        frames=iter((first,returning,idle,second))
        def read():bot.queue_observed_at=clock[0];return next(frames)
        bot.stable_queues=Mock(side_effect=read)
        armed=[]
        with patch('chat_invites.ChatWatcher') as watcher,patch('carriage_quota.CarriageQuota') as quota:
            w=watcher.return_value;w.images.visible.return_value=False;w.quota_dirty=False
            w.all_mining.side_effect=ChatWatcher.all_mining
            quota.return_value.check.return_value=4
            def chat_wait(rows,observed_at=None):
                armed.append((min(row.seconds for row in rows),observed_at))
                if len(armed)==1:clock[0]=observed_at+armed[-1][0]-5
                else:bot.stop_event.set()
            w.wait.side_effect=chat_wait
            with self.assertRaises(RunStopped):bot.run_continuous()
        self.assertEqual(armed,[(120,0.),(300,124.)])
        bot.dispatch_one.assert_called_once_with(continuous=True)
