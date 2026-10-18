"""Selected levels must be confirmed from pixels before searching."""
from pathlib import Path
import unittest
from unittest.mock import Mock,patch

from crystal_gather import CrystalGatherer,read_gather_config
from top_heroes import Match,WorkflowError,load_template

ROOT=Path(__file__).resolve().parents[1]


class LevelTests(unittest.TestCase):
    def bot(self,start=6,changes=True):
        config=read_gather_config(ROOT/'gather_crystal.yaml')
        config['recognition']='template'
        session=Mock();state={'level':start,'time':0,'pressed':[]}
        frames={n:load_template(ROOT/f'tests/fixtures/level_{n}_live.png') for n in (6,7)}
        session.capture.side_effect=lambda:frames[state['level']]
        bot=CrystalGatherer(config,ROOT,session,logger=lambda message:None)
        bot.level_region=lambda frame,plus:frame
        bot.wait=Mock(return_value=Match(.99,0,0,40,40))
        bot.poll=.01;bot.timeout=.05
        def sleep(seconds):state['time']+=seconds
        bot.sleep=sleep
        def press(name):
            state['pressed'].append(name)
            if changes:state['level']+=1 if name=='plus' else -1
        bot.press=press
        return bot,state,frames

    def test_default_is_seven_and_six_seven_labels_are_distinguished(self):
        bot,state,frames=self.bot()
        self.assertEqual(bot.config['target_level'],7)
        self.assertEqual(bot.read_level(frames[6],None),6)
        self.assertEqual(bot.read_level(frames[7],None),7)

    def test_six_to_seven_uses_one_plus_and_confirms_seven(self):
        bot,state,_=self.bot()
        with patch('crystal_gather.time.monotonic',side_effect=lambda:state['time']):
            bot.set_level(7)
        self.assertEqual(state['pressed'],['plus'])
        self.assertEqual(state['level'],7)

    def test_seven_to_six_uses_one_minus(self):
        bot,state,_=self.bot(7)
        with patch('crystal_gather.time.monotonic',side_effect=lambda:state['time']):
            bot.set_level(6)
        self.assertEqual(state['pressed'],['minus'])

    def test_unchanged_label_after_click_stops_instead_of_guessing_level(self):
        bot,state,_=self.bot(changes=False)
        with patch('crystal_gather.time.monotonic',side_effect=lambda:state['time']):
            with self.assertRaises(WorkflowError):bot.set_level(7)
        self.assertEqual(state['pressed'],['plus'])

    def test_unavailable_level_is_rejected_without_clicks(self):
        bot,state,_=self.bot()
        with self.assertRaises(WorkflowError):bot.set_level(99)
        self.assertEqual(state['pressed'],[])

    def test_all_eight_real_level_labels_are_distinguished(self):
        from yolo_dataset import read_image
        bot,_,_=self.bot()
        plus=Match(1,865,654,46,46)
        for n in range(1,9):
            frame=read_image(ROOT/f'captures/levels-20261015/level_{n}.jpg')[31:931,1:1601]
            self.assertEqual(bot.read_level(frame,plus),n)
