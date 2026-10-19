"""Reproduce the 05:42 obstruction without ever sending real mouse input."""
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

import numpy as np

import test_crystal_gather as helpers
from crystal_gather import QueueUnavailable, RunStopped
from queue_monitor import QueueReader, QueueRow
from top_heroes import Match, load_template
from visual_navigation import VisualNavigator, Page, PageUnavailable

ROOT=Path(__file__).resolve().parents[1]


class OccludedRecoveryTests(unittest.TestCase):
    def navigator(self, pages=None):
        bot,_=helpers.GatherTests().make_bot()
        bot.stop_event=threading.Event()
        bot.session=Mock()
        bot.session.generation=0
        bot.session.capture.return_value=np.zeros((900,1600,3),np.uint8)
        bot.log=Mock()
        bot.sleep=Mock()
        nav=VisualNavigator(bot)
        nav.observe=Mock(side_effect=pages) if pages else Mock(return_value=Page('世界地图'))
        nav.match=Mock(return_value=Match(.99,1532,849,41,22))
        return bot,nav

    def fake_clock(self, bot):
        clock=[0.]
        bot.sleep.side_effect=lambda seconds:clock.__setitem__(0,clock[0]+seconds)
        return patch('visual_navigation.time.monotonic',side_effect=lambda:clock[0])

    def test_saved_failure_frames_have_a_verified_return_city_target(self):
        bot,_=helpers.GatherTests().make_bot()
        nav=VisualNavigator(bot)
        files=list((ROOT/'tests/fixtures/occlusion_20261019').glob('*.png'))
        self.assertEqual(len(files),5)
        for path in files:
            with self.subTest(path=path.name):
                found=nav.match('return_city',load_template(path))
                self.assertIsNotNone(found)
                self.assertGreaterEqual(found.score,.96)
                self.assertTrue(1515<=found.center[0]<=1590 and 830<=found.center[1]<=880)

    def test_missing_button_never_counts_world_as_a_reset(self):
        bot,nav=self.navigator()
        nav.match.return_value=None
        with self.fake_clock(bot):
            with self.assertRaisesRegex(PageUnavailable,'不计为已恢复'):
                nav.ensure_world(reset=True)
        bot.session.click.assert_not_called()
        self.assertFalse(any('页面确认：世界地图' in c.args[0] for c in bot.log.call_args_list))

    def test_reset_requires_city_then_world_and_preserves_transition_wait(self):
        world=Page('世界地图')
        city=Page('城内','进入世界',(1550,838))
        bot,nav=self.navigator([world,world,world,world,city,city,world,world])
        self.assertIs(nav.ensure_world(reset=True),True)
        self.assertEqual(bot.session.click.call_args_list,[call((1552,860)),call((1550,838))])
        self.assertEqual(nav.observe.call_count,8)

    def test_clicked_button_without_city_confirmation_is_not_a_reset(self):
        bot,nav=self.navigator()
        with self.fake_clock(bot):
            with self.assertRaisesRegex(PageUnavailable,'未确认进入城内'):
                nav.ensure_world(reset=True)
        bot.session.click.assert_called_once()

    def test_one_city_frame_is_insufficient_confirmation(self):
        world=Page('世界地图')
        city=Page('城内','进入世界',(1550,838))
        bot,nav=self.navigator()
        nav.observe.side_effect=[world,world,city]+[world]*100
        with self.fake_clock(bot):
            with self.assertRaises(PageUnavailable):
                nav.ensure_world(reset=True)
        bot.session.click.assert_called_once()

    def test_occluded_first_row_cannot_authorize_refill(self):
        reader=QueueReader.__new__(QueueReader)
        reader.capacity=4
        from yolo_detector import Detection
        reader.detector=Mock()
        reader.detector.detect.return_value=[
            Detection('queue_header',Match(.99,20,160,35,16)),
            *[Detection('timer_region',Match(.95,55,y,71,26)) for y in (232,267,303)],
        ]
        reader.timer=Mock()
        self.assertIsNone(reader.read_yolo(np.zeros((900,1600,3),np.uint8)))
        reader.timer.read.assert_not_called()

    def test_successful_reset_resumes_monitoring_without_dispatch(self):
        bot,game=helpers.GatherTests().make_bot()
        bot.stop_event=threading.Event()
        bot.matches=lambda *args:[object()]
        bot.wait=Mock()
        bot.sleep=lambda seconds:bot.check_stop()
        bot.ensure_world=Mock(return_value=True)
        bot.expand_collapsed_queues=Mock(return_value=False)
        bot.stable_queues=Mock(side_effect=[QueueUnavailable('occluded')]*3+
                              [[QueueRow(i,'mining',30) for i in range(1,4)]])
        bot.on_event=lambda kind,data:bot.stop_event.set() if kind=='monitor' else None
        bot.dispatch_one=Mock()
        with self.assertRaises(RunStopped):bot.run_continuous()
        bot.ensure_world.assert_called_once_with(reset=True)
        bot.dispatch_one.assert_not_called()
        self.assertEqual(game.clicked,[])

    def test_reset_without_confirmation_cannot_increment_recovery_budget(self):
        bot,_=helpers.GatherTests().make_bot()
        bot.stop_event=threading.Event()
        bot.matches=lambda *args:[object()]
        bot.wait=Mock()
        bot.sleep=lambda seconds:bot.check_stop()
        bot.ensure_world=Mock(return_value=False)
        bot.expand_collapsed_queues=Mock(return_value=False)
        bot.stable_queues=Mock(side_effect=QueueUnavailable('occluded'))
        with self.assertRaisesRegex(PageUnavailable,'不计为队伍栏恢复'):
            bot.run_continuous()
        bot.ensure_world.assert_called_once_with(reset=True)
