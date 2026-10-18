from pathlib import Path
import cv2
from yolo_dataset import read_image
from crystal_gather import CrystalGatherer,read_gather_config
from unittest.mock import Mock
from top_heroes import Match

root=Path(__file__).resolve().parent
for n in (1,2,3,4,5,8):
 image=read_image(root/f'captures/levels-20261015/level_{n}.jpg')
 cv2.imencode('.png',image[696:718,941:978])[1].tofile(str(root/f'templates/levels/level_{n}.png'))
config=read_gather_config(root/'gather_crystal.yaml');config['recognition']='template'
bot=CrystalGatherer(config,root,Mock(),logger=lambda message:None)
for n in range(1,9):
 frame=read_image(root/f'captures/levels-20261015/level_{n}.jpg')[31:931,1:1601]
 plus=Match(1,865,654,46,46)
 value=bot.read_level(frame,plus)
 print(n,value)
 assert value==n
