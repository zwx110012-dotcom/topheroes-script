"""Build reviewed pixel crops from locally captured client screenshots."""
from pathlib import Path
import hashlib,json
import cv2,numpy as np
from yolo_dataset import read_image

ROOT=Path(__file__).resolve().parent
directory=ROOT/'templates/carriage'
directory.mkdir(exist_ok=True)
entry=read_image(ROOT/'captures/carriage-20261015/01-city-carriage-entry.jpg')[31:931,1:1601]
page=read_image(ROOT/'captures/carriage-20261015/02-my-carriage-zero.jpg')[31:931,1:1601]
world=read_image(ROOT/'captures/carriage-20261015/03-world-return-city.jpg')[31:931,1:1601]
images={
 'trade_label':page[20:46,570:708],
 'carriage_building':entry[712:754,986:1031],
 'carriage_menu':entry[664:703,1007:1044],
 'return_city':world[847:870,1530:1581],
 'my_tab':page[850:874,702:775],
 'back':read_image(ROOT/'templates/chat/chat_close.png'),
}
assets={}
for name,array in images.items():
 path=directory/f'{name}.png';cv2.imencode('.png',array)[1].tofile(str(path))
 spec={'file':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'confidence':.94}
 if name in ('trade_label','my_tab','back'):spec['roi']=[.33,0,.67,1]
 if name=='return_city':spec['roi']=[.9,.85,1,1]
 if name=='carriage_building':spec['scales']=[.85,.9,.95,1,1.05,1.1,1.15]
 assets[name]=spec
(directory/'assets.json').write_text(json.dumps({'stage':'verified','assets':assets},indent=2),encoding='utf-8')
