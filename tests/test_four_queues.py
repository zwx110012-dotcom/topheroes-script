"""Four-row state parsing and refill accounting must preserve unknown rows."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch,Mock

import numpy as np
from queue_monitor import QueueReader,QueueRow
from yolo_detector import Detection,YoloDetector
from top_heroes import Match,WorkflowError
from crystal_gather import RunStopped
import test_crystal_gather as gather_tests


class FourQueueTests(unittest.TestCase):
    def test_fourth_returning_row_is_not_assigned_to_third(self):
        items=[Detection('queue_header',Match(.95,20,160,35,16)),
               Detection('timer_region',Match(.95,60,194,70,31)),
               Detection('idle',Match(.95,70,239,44,16)),
               Detection('timer_region',Match(.95,60,266,70,31)),
               Detection('timer_region',Match(.95,60,302,70,31)),
               Detection('queue_mining',Match(.95,30,212,18,18)),
               Detection('queue_mining',Match(.95,30,284,18,18)),
               Detection('queue_returning',Match(.95,30,320,18,18))]
        reader=QueueReader.__new__(QueueReader);reader.capacity=4
        reader.detector=Mock();reader.detector.detect.return_value=items
        reader.timer=Mock();reader.timer.read.return_value=(42,'00:00:42',.98)
        frame=np.zeros((900,1600,3),np.uint8)
        rows=reader.read_yolo(frame)
        self.assertEqual([r.state for r in rows],['mining','idle','mining','returning'])
        self.assertEqual([r.number for r in rows],[1,2,3,4])
        items.pop(4)
        self.assertIsNone(reader.read_yolo(frame))  # Fourth row missing is not idle.

    @patch('crystal_gather.time.sleep')
    def test_single_round_dispatches_four_and_confirms_each(self,_sleep):
        bot,game=gather_tests.GatherTests().make_bot(idle=4)
        bot.capacity=4;bot.config.update(queue_capacity=4,dispatches=4)
        bot.run()
        self.assertEqual(bot.completed,4)
        self.assertEqual(game.clicked.count('depart'),4)
        self.assertEqual(game.idle,0)

    def test_continuous_target_four_refills_one_when_three_busy(self):
        bot,game=gather_tests.GatherTests().make_bot()
        bot.capacity=4;bot.config.update(queue_capacity=4,dispatches=4)
        bot.stop_event=threading.Event();bot.matches=lambda *args:[object()]
        bot.wait=lambda *args:object();bot.sleep=lambda seconds:bot.check_stop()
        bot.logger=lambda message:None
        rows=[QueueRow(i,'mining',30) for i in range(1,4)]+[QueueRow(4,'idle')]
        bot.stable_queues=Mock(side_effect=[rows,[QueueRow(i,'mining',30) for i in range(1,5)]])
        bot.dispatch_one=Mock()
        def event(kind,data):
            if kind=='monitor' and data['delay']!=0:bot.stop_event.set()
        bot.on_event=event
        with self.assertRaises(RunStopped):bot.run_continuous()
        bot.dispatch_one.assert_called_once_with(continuous=True)

    def test_three_queue_model_cannot_authorize_four_queue_clicks(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'topheroes.onnx'
            p.with_suffix('.json').write_text(json.dumps({'supported_queue_counts':[3]}))
            d=YoloDetector.__new__(YoloDetector);d.path=p;d.queue_detector=None
            d.require_capacity(3)
            with self.assertRaises(WorkflowError):d.require_capacity(4)
            p.with_suffix('.json').write_text(json.dumps({'supported_queue_counts':[3,4]}))
            d.require_capacity(4)

    def test_idle_label_with_busy_badge_cannot_authorize_a_refill(self):
        items=[Detection('queue_header',Match(.95,20,160,35,16)),
               *[Detection('idle',Match(.95,66,y,44,16)) for y in (202,238,274,310)],
               Detection('queue_mining',Match(.95,30,278,18,18))]
        reader=QueueReader.__new__(QueueReader);reader.capacity=4
        reader.detector=Mock();reader.detector.detect.return_value=items
        self.assertIsNone(reader.read_yolo(np.zeros((900,1600,3),np.uint8)))
