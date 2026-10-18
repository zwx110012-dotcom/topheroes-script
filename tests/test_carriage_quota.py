from pathlib import Path
import unittest
from unittest.mock import Mock,patch
import numpy as np

from carriage_quota import CarriageImages
from crystal_gather import CrystalGatherer
from queue_monitor import QueueRow
from yolo_dataset import read_image

ROOT=Path(__file__).resolve().parents[1]


class CarriageTests(unittest.TestCase):
    def test_disabled_monitor_skips_all_carriage_and_chat_work(self):
        bot=CrystalGatherer.__new__(CrystalGatherer)
        bot.config={'chat_invites':False,'dispatches':4};bot.capacity=4;bot.completed=0
        bot.session=Mock();bot.require_ready=Mock();bot.check_stop=Mock()
        bot.matches=Mock(return_value=[Mock()]);bot.wait=Mock();bot.log=Mock();bot.emit=Mock()
        bot.stable_queues=Mock(return_value=[QueueRow(i,'mining',30) for i in range(1,5)])
        bot.sleep=Mock(side_effect=RuntimeError('end monitor'))
        with patch('chat_invites.ChatWatcher') as watcher,patch('carriage_quota.CarriageQuota') as quota:
            with self.assertRaisesRegex(RuntimeError,'end monitor'):bot.run_continuous()
            watcher.assert_not_called()
            quota.assert_not_called()

    def test_real_zero_counter_and_unknown_counter(self):
        images=CarriageImages(ROOT)
        frame=read_image(ROOT/'captures/carriage-20261015/02-my-carriage-zero.jpg')[31:931,1:1601]
        self.assertEqual(images.read(frame),(0,4))
        frame[20:46,708:748]=0
        self.assertIsNone(images.read(frame))
        self.assertIsNone(images.read(np.zeros_like(frame)))

    def test_moved_city_building_is_still_located(self):
        images=CarriageImages(ROOT)
        frame=read_image(ROOT/'captures/carriage-20261015/04-city-shifted.jpg')[31:931,1:1601]
        matches=images.matches('carriage_building',frame)
        self.assertEqual(len(matches),1)
        self.assertTrue(1070<matches[0].center[0]<1140)

    def test_zero_or_unknown_quota_never_enters_chat(self):
        for remaining in (0,None):
            bot=CrystalGatherer.__new__(CrystalGatherer)
            bot.config={'chat_invites':True,'dispatches':4};bot.capacity=4;bot.completed=0
            bot.session=Mock();bot.require_ready=Mock();bot.check_stop=Mock()
            bot.matches=Mock(return_value=[Mock()]);bot.wait=Mock();bot.log=Mock();bot.emit=Mock()
            bot.stable_queues=Mock(return_value=[QueueRow(i,'mining',30) for i in range(1,5)])
            bot.sleep=Mock(side_effect=RuntimeError('end monitor'))
            with patch('chat_invites.ChatWatcher') as watcher,patch('carriage_quota.CarriageQuota') as quota:
                quota.return_value.check.return_value=remaining
                with self.assertRaisesRegex(RuntimeError,'end monitor'):bot.run_continuous()
                watcher.return_value.wait.assert_not_called()
                quota.return_value.check.assert_called_once()
