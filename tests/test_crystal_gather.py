"""Exercise dispatch counting and failure handling with a pixel-only game simulator."""

import copy
import unittest
import threading
from pathlib import Path
from unittest.mock import patch

import numpy as np
import cv2

from crystal_gather import CrystalGatherer, RunStopped, read_gather_config, find_matches
from top_heroes import WorkflowError, load_template


BASE = Path(__file__).resolve().parents[1]


class PixelGame:
    positions = {"world": (520, 320), "search_icon": (20, 250),
                 "resources": (235, 75), "red_crystal": (350, 140),
                 "plus": (280, 200), "minus": (80, 200),
                 "level_six": (359, 213), "search_button": (250, 300),
                 "gather": (250, 200), "depart": (250, 240)}

    def __init__(self, templates, idle=3, acknowledge=True):
        self.templates = templates
        self.state, self.idle = "city", idle
        self.acknowledge = acknowledge
        self.clicked = []

    def visible(self):
        return {"city": ["world"], "world": ["search_icon"],
                "menu": ["resources", "red_crystal", "plus", "minus", "level_six", "search_button"],
                "point": ["gather"], "march": ["depart"]}[self.state]

    def capture(self):
        frame = np.full((400, 600, 3), 35, dtype=np.uint8)
        for name in self.visible():
            x, y = self.positions[name]
            image = self.templates[name]
            h, w = image.shape[:2]
            frame[y:y + h, x:x + w] = image
        if self.state == "world":
            image = self.templates["idle"]
            h, w = image.shape[:2]
            for index in range(self.idle):
                y = 45 + 30 * index
                frame[y:y + h, 70:70 + w] = image
        return frame

    def click(self, point):
        for name in self.visible():
            x, y = self.positions[name]
            h, w = self.templates[name].shape[:2]
            if x <= point[0] < x + w and y <= point[1] < y + h:
                self.clicked.append(name)
                if name == "world":
                    self.state = "world"
                elif name == "search_icon":
                    self.state = "menu"
                elif name == "search_button":
                    self.state = "point"
                elif name == "gather":
                    self.state = "march"
                elif name == "depart":
                    if self.acknowledge:
                        self.idle -= 1
                    self.state = "world"
                return
        raise AssertionError(f"Click without a visible target: {point}")


class GatherTests(unittest.TestCase):
    def make_bot(self, idle=3, acknowledge=True):
        config = copy.deepcopy(read_gather_config(BASE / "gather_crystal.yaml"))
        config.update(queue_capacity=3, dispatches=3, target_level=6, chat_invites=False, scales=[1.0], timeout_seconds=0.5, click_settle_seconds=0,
                      search_wait_seconds=2, poll_seconds=0.01)
        for name, spec in config["templates"].items():
            spec.pop("roi", None)
        config["templates"]["idle"]["roi"] = [0, 0, 0.3, 0.4]
        templates = {name: load_template(BASE / spec["path"])
                     for name, spec in config["templates"].items()}
        session = PixelGame(templates, idle, acknowledge)
        return CrystalGatherer(config, BASE, session), session

    @patch("crystal_gather.time.sleep")
    def test_one_big_cycle_has_three_confirmed_departures(self, sleep):
        bot, game = self.make_bot()
        bot.run()
        self.assertEqual(game.clicked.count("depart"), 3)
        self.assertEqual(game.clicked.count("search_button"), 3)
        self.assertEqual(game.idle, 0)
        self.assertNotIn("minus", game.clicked)
        self.assertNotIn("plus", game.clicked)
        self.assertEqual(sum(call.args == (2.0,) for call in sleep.call_args_list), 3)

    @patch("crystal_gather.time.sleep")
    def test_full_queue_stops_before_opening_search(self, _sleep):
        bot, game = self.make_bot(idle=0)
        with self.assertRaisesRegex(WorkflowError, "空闲队列"):
            bot.run()
        self.assertEqual(game.clicked, ["world"])

    @patch("crystal_gather.time.sleep")
    def test_failed_departure_is_never_retried(self, _sleep):
        bot, game = self.make_bot(acknowledge=False)
        with self.assertRaisesRegex(WorkflowError, "不重复点击出发"):
            bot.run()
        self.assertEqual(game.clicked.count("depart"), 1)

    def test_preview_never_clicks(self):
        bot, game = self.make_bot()
        bot.preview()
        self.assertEqual(game.clicked, [])

    def test_actual_queue_crops_count_two_one_and_zero(self):
        bot, _game = self.make_bot()
        bot.scales = read_gather_config(BASE / "gather_crystal.yaml")["scales"]
        bot.specs["idle"]["roi"] = [0, 0, 1, 1]
        for count in (2, 1, 0):
            with self.subTest(count=count):
                frame = load_template(BASE / f"tests/fixtures/queues_{count}.png")
                self.assertEqual(bot.idle_count(frame), count)

    def test_actual_1600_search_panel_and_level_labels(self):
        bot, _game = self.make_bot()
        bot.scales = read_gather_config(BASE / "gather_crystal.yaml")["scales"]
        for spec in bot.specs.values():
            spec["roi"] = [0, 0, 1, 1]
        for filename, is_six in [("crystal_search_1600.png", True),
                                  ("crystal_search_level5_1600.png", False)]:
            with self.subTest(filename=filename):
                frame = load_template(BASE / "tests/fixtures" / filename)
                for name in ("resources", "red_crystal", "minus", "plus", "search_button"):
                    self.assertTrue(bot.matches(name, frame), name)
                plus = bot.matches("plus", frame)[0]
                self.assertEqual(bot.is_six(frame, plus), is_six)

    def test_actual_1600_gather_and_departure_buttons(self):
        bot, _game = self.make_bot()
        for spec in bot.specs.values():
            spec["roi"] = [0, 0, 1, 1]
        for name in ("gather", "depart"):
            frame = load_template(BASE / f"tests/fixtures/crystal_{name}_1600.png")
            self.assertTrue(bot.matches(name, frame), name)

    @patch("crystal_gather.time.monotonic", side_effect=[0, 1])
    def test_timeout_reports_score_and_retains_diagnostic_frame(self, _clock):
        bot, game = self.make_bot()
        events = []
        bot.on_event = lambda kind, data: events.append((kind, data))
        with self.assertRaisesRegex(WorkflowError, "最高得分"):
            bot.wait("plus")
        self.assertEqual(game.clicked, [])
        self.assertEqual(events[0][0], "diagnostic")
        self.assertEqual(events[0][1]["name"], "plus")
        self.assertIsNotNone(events[0][1]["match"])

    @patch("crystal_gather.time.sleep")
    def test_three_dispatches_with_subpixel_text_in_each_row(self, _sleep):
        bot, game = self.make_bot()
        capture = game.capture
        def antialiased_capture():
            frame = capture()
            if game.state == "world":
                for index in range(game.idle):
                    y = 45 + 30 * index
                    crop = frame[y - 4:y + 20, 66:118].copy()
                    shift = np.float32([[1, 0, 0.5], [0, 1, 0.5]])
                    frame[y - 4:y + 20, 66:118] = cv2.warpAffine(
                        crop, shift, (52, 24), borderMode=cv2.BORDER_REPLICATE)
            return frame
        game.capture = antialiased_capture
        game.state = "world"
        before = game.capture()
        self.assertEqual(len(find_matches(before, bot.templates["idle"], [1], 0.85,
                                          [0, 0, 0.3, 0.4])), 0)
        self.assertEqual(bot.idle_count(before), 3)
        game.state = "city"
        bot.run()
        self.assertEqual(game.clicked.count("depart"), 3)
        self.assertEqual(bot.completed, 3)

    def test_stop_requested_before_run_never_clicks(self):
        bot, game = self.make_bot()
        bot.stop_event = threading.Event()
        bot.stop_event.set()
        with self.assertRaises(RunStopped):
            bot.run()
        self.assertEqual(game.clicked, [])

    @patch("crystal_gather.time.sleep")
    def test_stop_after_first_click_blocks_next_click(self, _sleep):
        bot, game = self.make_bot()
        bot.stop_event = threading.Event()
        original_click = game.click
        def stop_on_click(point):
            original_click(point)
            bot.stop_event.set()
        game.click = stop_on_click
        with self.assertRaises(RunStopped):
            bot.run()
        self.assertEqual(game.clicked, ["world"])


if __name__ == "__main__":
    unittest.main()
