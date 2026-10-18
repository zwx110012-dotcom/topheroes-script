"""Queue read retries must never authorize clicks or retry a departure."""
import threading
import unittest
from unittest.mock import Mock

from crystal_gather import QueueUnavailable,RunStopped,NoIdleQueues
from queue_monitor import QueueRow
from top_heroes import WorkflowError
import test_crystal_gather as gather_tests


class QueueRecoveryTests(unittest.TestCase):
    def make_bot(self):
        bot,game=gather_tests.GatherTests().make_bot()
        bot.stop_event=threading.Event()
        bot.matches=lambda *args:[object()]
        bot.wait=lambda *args:object()
        bot.sleep=lambda seconds:bot.check_stop()
        bot.logger=lambda message:None
        return bot,game

    def test_read_timeout_waits_then_monitors_without_clicking(self):
        bot,game=self.make_bot();waits=[]
        bot.stable_queues=Mock(side_effect=[QueueUnavailable('missing'),
            [QueueRow(i,'mining',30) for i in range(1,4)]])
        def event(kind,data):
            if kind=='queue_wait':waits.append(data['delay'])
            if kind=='monitor':bot.stop_event.set()
        bot.on_event=event
        with self.assertRaises(RunStopped):bot.run_continuous()
        self.assertEqual(waits,[10]);self.assertEqual(game.clicked,[])

    def test_second_read_before_dispatch_can_wait_without_departure(self):
        bot,game=self.make_bot();waits=[]
        bot.stable_queues=Mock(side_effect=[
            [QueueRow(1,'idle'),QueueRow(2,'mining',30),QueueRow(3,'mining',40)],
            [QueueRow(i,'mining',30) for i in range(1,4)]])
        bot.dispatch_one=Mock(side_effect=QueueUnavailable('missing before search'))
        def event(kind,data):
            if kind=='queue_wait':waits.append(data['delay'])
            if kind=='monitor' and data['delay']!=0:bot.stop_event.set()
        bot.on_event=event
        with self.assertRaises(RunStopped):bot.run_continuous()
        self.assertEqual(waits,[10]);self.assertEqual(game.clicked,[])
        bot.dispatch_one.assert_called_once_with(continuous=True)

    def test_f8_interrupts_queue_retry_before_any_click(self):
        bot,game=self.make_bot()
        bot.stable_queues=Mock(side_effect=QueueUnavailable('missing'))
        def event(kind,data):
            if kind=='queue_wait':bot.stop_event.set()
        bot.on_event=event
        with self.assertRaises(RunStopped):bot.run_continuous()
        self.assertEqual(game.clicked,[])

    def test_no_idle_at_final_predeparture_check_rechecks_without_dispatch(self):
        bot,game=self.make_bot();delays=[]
        bot.stable_queues=Mock(side_effect=[
            [QueueRow(1,'idle'),QueueRow(2,'mining',30),QueueRow(3,'mining',40)],
            [QueueRow(i,'mining',30) for i in range(1,4)]])
        bot.dispatch_one=Mock(side_effect=NoIdleQueues('no idle before search'))
        def event(kind,data):
            if kind=='queue_recheck':delays.append(data['delay'])
            if kind=='monitor' and data['delay']!=0:bot.stop_event.set()
        bot.on_event=event
        with self.assertRaises(RunStopped):bot.run_continuous()
        self.assertEqual(delays,[2]);self.assertEqual(game.clicked,[])
        bot.dispatch_one.assert_called_once_with(continuous=True)

    def test_failed_departure_confirmation_still_stops(self):
        bot,game=self.make_bot();waits=[]
        bot.stable_queues=lambda:[QueueRow(1,'idle'),QueueRow(2,'mining',30),QueueRow(3,'mining',40)]
        bot.dispatch_one=Mock(side_effect=WorkflowError('departure not confirmed'))
        bot.on_event=lambda kind,data:waits.append(kind)
        with self.assertRaisesRegex(WorkflowError,'departure not confirmed'):bot.run_continuous()
        self.assertNotIn('queue_wait',waits)
        bot.dispatch_one.assert_called_once_with(continuous=True)
