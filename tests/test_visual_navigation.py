"""Page transitions, false negatives and departure reconciliation."""
import threading
import unittest
from unittest.mock import Mock,patch
from pathlib import Path
import numpy as np

from crystal_gather import DepartureUncertain,RunStopped
from visual_navigation import VisualNavigator,Page,PageUnavailable
from queue_monitor import QueueRow
from top_heroes import Match,load_template,WindowChanged
import test_crystal_gather as helpers

ROOT=Path(__file__).resolve().parents[1]


class NavigationTests(unittest.TestCase):
    def test_start_in_world_does_not_wait_for_city_world_button(self):
        bot,game=helpers.GatherTests().make_bot(idle=1)
        game.state='world'
        with patch('crystal_gather.time.sleep'):bot.dispatch_one()
        self.assertNotIn('world',game.clicked)
        self.assertEqual(game.clicked.count('depart'),1)

    def test_yolo_missed_navigation_has_pixel_fallback(self):
        bot,game=helpers.GatherTests().make_bot()
        game.state='world'
        bot.detector=Mock();bot.detector.matches.return_value=[]
        self.assertTrue(bot.matches('search_icon',game.capture()))
        game.state='city'
        self.assertTrue(bot.matches('world',game.capture()))

    def test_real_search_panel_has_a_safe_outside_dismiss_point(self):
        bot,_=helpers.GatherTests().make_bot()
        frame=load_template(ROOT/'captures/levels-20261015/level_7.jpg')[31:931,1:1601]
        nav=VisualNavigator(bot)
        page=nav.observe(frame)
        self.assertEqual(page.name,'资源搜索')
        self.assertLess(page.point[0],560)
        self.assertGreater(page.point[0],500)

    def test_folded_queue_has_explicit_arrow_evidence(self):
        bot,_=helpers.GatherTests().make_bot()
        nav=VisualNavigator(bot)
        folded=load_template(ROOT/'captures/recovery-20261017/04-world-queue-collapsed.jpg')[31:931,1:1601]
        expanded=load_template(ROOT/'captures/recovery-20261017/01-world.jpg')[31:931,1:1601]
        self.assertEqual(nav.observe(folded).action,'展开队伍列表')
        self.assertIsNone(nav.match('queue_collapsed',expanded))

    def test_newly_unlocked_levels_are_read_before_adjustment(self):
        bot,_=helpers.GatherTests().make_bot()
        plus=Match(1,865,654,46,46)
        for n in (9,10):
            frame=load_template(ROOT/f'captures/recovery-20261017/0{n-7}-search-level{n}.jpg')[31:931,1:1601]
            self.assertEqual(bot.read_level(frame,plus),n)

    def navigator(self,pages):
        bot,_=helpers.GatherTests().make_bot()
        bot.stop_event=threading.Event();bot.session=Mock()
        bot.session.generation=0
        bot.session.capture.return_value=np.zeros((900,1600,3),np.uint8)
        nav=VisualNavigator(bot);nav.observe=Mock(side_effect=pages)
        bot.sleep=Mock();bot.log=Mock()
        return bot,nav

    def test_search_popup_is_closed_before_returning_world(self):
        modal=Page('资源搜索','关闭搜索弹窗',(540,395));world=Page('世界地图')
        bot,nav=self.navigator([modal,modal,world,world])
        nav.ensure_world()
        bot.session.click.assert_called_once_with((540,395))

    def test_moving_window_discards_old_observation(self):
        city=Page('城内','进入世界',(1550,840));world=Page('世界地图')
        bot,nav=self.navigator([city,city,city,city,world,world])
        bot.session.click.side_effect=[WindowChanged('moved'),None]
        nav.ensure_world()
        self.assertEqual(nav.observe.call_count,6)

    def test_unknown_screen_waits_without_blind_clicks(self):
        bot,nav=self.navigator([]);nav.observe.side_effect=None
        nav.observe.return_value=Page('未知画面')
        clock=[0]
        bot.sleep.side_effect=lambda seconds:clock.__setitem__(0,clock[0]+seconds)
        with patch('visual_navigation.time.monotonic',side_effect=lambda:clock[0]):
            with self.assertRaises(PageUnavailable):nav.ensure_world()
        bot.session.click.assert_not_called()
        self.assertGreaterEqual(clock[0],8)

    def test_stop_interrupts_recovery_before_click(self):
        city=Page('城内','进入世界',(1550,840))
        bot,nav=self.navigator([city])
        bot.sleep.side_effect=lambda seconds:bot.stop_event.set()
        with self.assertRaises(RunStopped):nav.ensure_world()
        bot.session.click.assert_not_called()

    def test_unconfirmed_departure_is_reconciled_without_redispatch(self):
        bot,game=helpers.GatherTests().make_bot()
        bot.stop_event=threading.Event();bot.logger=lambda message:None
        bot.matches=Mock(return_value=[Mock()]);bot.ensure_world=Mock()
        bot.wait=Mock();bot.sleep=lambda seconds:bot.check_stop()
        idle=[QueueRow(1,'idle'),QueueRow(2,'mining',40),QueueRow(3,'mining',60)]
        busy=[QueueRow(i,'mining',30) for i in range(1,4)]
        bot.stable_queues=Mock(side_effect=[idle,busy,busy])
        bot.dispatch_one=Mock(side_effect=DepartureUncertain('pending',1,{1}))
        bot.confirm_departure=Mock()
        def event(kind,data):
            if kind=='monitor' and bot.completed==1:bot.stop_event.set()
        bot.on_event=event
        with self.assertRaises(RunStopped):bot.run_continuous()
        bot.dispatch_one.assert_called_once_with(continuous=True)
        bot.confirm_departure.assert_called_once_with(1,{1})
        self.assertEqual(bot.completed,1)
        self.assertEqual(game.clicked,[])
