"""Real pixel crops and virtual-time mining -> return -> refill regressions."""

import math
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from crystal_gather import RunStopped
from queue_monitor import QueueReader, QueueRow, TimerReader, next_check_seconds, text_glyphs
from top_heroes import load_template, WorkflowError
import test_crystal_gather as gather_tests
from test_crystal_gather import PixelGame

BASE = Path(__file__).resolve().parents[1]


def fixture(name):
    crop = load_template(BASE / "tests/fixtures" / name)
    frame = np.zeros((900, 1600, 3), np.uint8)
    frame[:crop.shape[0], :crop.shape[1]] = crop
    return frame


class QueuePixelTests(unittest.TestCase):
    def setUp(self):
        self.reader = QueueReader(BASE, load_template(BASE / "templates/idle.png"))

    def test_actual_mining_returning_idle_and_all_ten_digits(self):
        for name, expected, times in [
            ("queue_returning_1600.png", ["mining", "mining", "returning"], [946, 277, 20]),
            ("queue_idle_1600.png", ["mining", "mining", "idle"], [901, 232, None]),
            ("queue_mining_1600.png", ["mining"] * 3, [2617, 1948, 1663])]:
            with self.subTest(name=name):
                rows = self.reader.read(fixture(name))
                self.assertEqual([r.state for r in rows], expected)
                self.assertEqual([r.seconds for r in rows], times)
        user = load_template(BASE / "tests/fixtures/timers_user.png")
        for i, expected in enumerate((1079, 410, 125)):
            result = self.reader.timer.read(user[12+i*36:37+i*36,54:122])
            self.assertIsNotNone(result)
            self.assertEqual(result[0], expected)

    def test_actual_script_capture_and_untrained_timer_frames(self):
        for name,times in [("queue_pillow_1600.png",[2786,2039,1759]),
                           ("queue_pillow_holdout.png",[2649,1902,1622]),
                           ("queue_pillow_holdout2.png",[2621,1874,1594])]:
            with self.subTest(name=name):
                rows = self.reader.read(fixture(name))
                self.assertEqual([r.state for r in rows],["mining"]*3)
                self.assertEqual([r.seconds for r in rows],times)

    def test_new_size_return_font_is_read_without_guessing(self):
        image=load_template(BASE / "tests/fixtures/timer_return_1530.png")
        result=self.reader.timer.read(image)
        self.assertIsNotNone(result)
        self.assertEqual(result[0],9)

    def test_outbound_timer_is_distinguished_from_mining_and_returning(self):
        rows = self.reader.read(fixture("queue_outbound_1600.png"))
        self.assertEqual([r.state for r in rows],["mining","outbound","outbound"])
        self.assertEqual([r.seconds for r in rows],[2815,3,20])

    def test_portrait_changes_do_not_change_status_badge_recognition(self):
        frame = fixture("queue_returning_1600.png")
        rng = np.random.default_rng(5)
        for i in range(3):
            y = 196 + 36*i
            frame[y:y+27,14:27] = rng.integers(0,256,(27,13,3),dtype=np.uint8)
            # Also change pixels behind badge corners excluded by the circle mask.
            yy, xx = np.ogrid[:15,:16]
            outside = ((xx-7.5)**2+(yy-7)**2)>6.3**2
            badge = frame[208+36*i:223+36*i,28:44]
            badge[outside] = 0
        self.assertEqual([r.state for r in self.reader.read(frame)],
                         ["mining", "mining", "returning"])

    def test_folded_list_and_bad_timer_never_become_idle(self):
        frame = fixture("queue_mining_1600.png")
        frame[190:310,:180] = 0
        self.assertIsNone(self.reader.read(frame))
        frame = fixture("queue_mining_1600.png")
        frame[196:221,55:125] = 0
        rows = self.reader.read(frame)
        self.assertEqual(rows[0].state, "mining")
        self.assertIsNone(rows[0].seconds)
        self.assertEqual(next_check_seconds(rows), 2)
        self.assertIsNone(self.reader.timer.read(np.zeros((25,70,3),np.uint8)))

    def test_alarm_uses_earliest_timer_and_near_zero_rechecks(self):
        rows = [QueueRow(1,"mining",100), QueueRow(2,"returning",8), QueueRow(3,"mining",50)]
        self.assertEqual(next_check_seconds(rows),7)
        rows[1] = QueueRow(2,"returning",0)
        self.assertEqual(next_check_seconds(rows),0.5)
        rows[1] = QueueRow(2,"mining",100)
        self.assertEqual(next_check_seconds(rows),30)


class VirtualClock:
    now = 0.0

    def sleep(self, seconds):
        self.now += seconds
        if self.now > 20:
            raise AssertionError("Loop failed to refill after a return")


class ContinuousPixelGame(PixelGame):
    def __init__(self, templates, clock):
        super().__init__(templates, idle=0)
        self.state = "world"
        self.clock, self.sent = clock, False
        self.depart_times = []
        self.mine = fixture("queue_mining_1600.png")
        self.returning = fixture("queue_returning_1600.png")
        self.free = fixture("queue_idle_1600.png")
        self.glyphs = {}
        # Use real glyph shapes to render changing, valid HH:MM:SS pixels.
        user = load_template(BASE / "tests/fixtures/timers_user.png")
        samples = [(self.mine,196,"00:43:37",55), (self.mine,232,"00:32:28",55),
                   (self.mine,268,"00:27:43",55),
                   (user,12,"00:17:59",54), (user,48,"00:06:50",54)]
        for image,y,text,x in samples:
            for char,glyph in zip(text,text_glyphs(image[y:y+25,x:x+70])):
                self.glyphs.setdefault(char,glyph)

    def capture(self):
        old = super().capture()
        frame = np.full((900,1600,3),35,np.uint8)
        frame[:400,:600] = old
        frame[:310,:180] = self.mine[:310,:180]
        # Keep the observed world-search button outside the queue crop.
        if self.state == "world":
            image = self.templates["search_icon"]
            h,w = image.shape[:2]
            frame[630:630+h,20:20+w] = image
        if not self.sent:
            if self.clock.now < 2:
                seconds = max(0,math.ceil(2-self.clock.now))
            elif self.clock.now < 3.5:
                frame[268:305,:156] = self.returning[268:305,:156]
                seconds = max(0,math.ceil(3.5-self.clock.now))
            else:
                frame[268:305,:156] = self.free[268:305,:156]
                self.idle = 1
                return frame
        else:
            seconds = 100
        text = f"00:{seconds//60:02}:{seconds%60:02}"
        frame[268:293,55:125] = (25,50,30)
        x = 60
        for char in text:
            glyph = self.glyphs[char]
            h,w = glyph.shape
            y = 276 if char == ':' else 274
            crop = frame[y:y+h,x:x+w]
            crop[glyph>0] = 255
            x += w+2
        return frame

    def click(self, point):
        if self.state == "world" and 20 <= point[0] <= 60 and 630 <= point[1] <= 675:
            self.clicked.append("search_icon")
            self.state = "menu"
            return
        count = self.clicked.count("depart")
        super().click(point)
        if self.clicked.count("depart") > count:
            self.depart_times.append(self.clock.now)
            self.sent = True


class ContinuousFlowTests(unittest.TestCase):
    def test_mining_zero_returning_then_idle_refills_once_and_keeps_monitoring(self):
        bot, _ = gather_tests.GatherTests().make_bot()
        clock = VirtualClock()
        game = ContinuousPixelGame(bot.templates, clock)
        bot.session, bot.stop_event = game, threading.Event()
        bot.config["search_wait_seconds"] = 0
        observed = []
        def event(kind,data):
            if kind == "monitor":
                observed.append([r.state for r in data["rows"]])
                if bot.completed == 1:
                    bot.stop_event.set()
        bot.on_event = event
        def sleep(seconds):
            bot.check_stop()
            clock.sleep(seconds)
        with patch("crystal_gather.time.monotonic",side_effect=lambda:clock.now), \
             patch.object(bot,"sleep",side_effect=sleep):
            with self.assertRaises(RunStopped):
                bot.run_continuous()
        self.assertEqual(bot.completed,1)
        self.assertEqual(game.clicked.count("depart"),1)
        self.assertGreaterEqual(game.depart_times[0],3.5)
        self.assertIn(["mining","mining","returning"],observed)
        self.assertIn(["mining","mining","idle"],observed)
        self.assertEqual(observed[-1],["mining"]*3)

    def test_stop_interrupts_a_distant_alarm_without_another_click(self):
        bot, game = gather_tests.GatherTests().make_bot()
        bot.stop_event = threading.Event()
        timer = threading.Timer(0.02,bot.stop_event.set)
        timer.start()
        try:
            with self.assertRaises(RunStopped):
                bot.sleep(3600)
        finally:
            timer.join()
        self.assertEqual(game.clicked,[])

    def test_simultaneous_other_return_does_not_hide_departure(self):
        bot, game = gather_tests.GatherTests().make_bot(idle=1)
        bot.queue_reader = type("Reader",(),{"read":lambda self,frame:[
            QueueRow(1,"idle"),QueueRow(2,"busy",40),QueueRow(3,"mining",50)]})()
        bot.matches = lambda name,frame=None,roi=None: [] if name == "depart" else [object()]
        bot.idle_count = lambda frame: 1  # Idle total stayed unchanged as another team arrived.
        with patch.object(bot,"sleep"):
            bot.confirm_departure(1,{2})
        self.assertEqual(game.clicked,[])


if __name__ == "__main__":
    unittest.main()
