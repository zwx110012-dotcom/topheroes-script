"""Independent image checks for the merchant specialist; no live input."""
from pathlib import Path
import json
from yolo_detector import YoloDetector
from yolo_dataset import read_image,load_boxes

ROOT=Path(__file__).resolve().parent
detector=YoloDetector(ROOT/'models/topheroes-merchant-candidate.onnx')
report={}
for name in ('merchant-join-user1','merchant-join-user2'):
 frame=read_image(ROOT/f'datasets/topheroes/images/{name}.png')
 found=detector.detect(frame)
 expected=load_boxes(ROOT/f'datasets/topheroes/labels/{name}.txt',frame.shape[1],frame.shape[0])
 items=[]
 for cls,x0,y0,x1,y1 in expected:
  if cls!='merchant_join':continue
  matches=[d.match for d in found if d.name==cls]
  if len(matches)!=1:raise ValueError(f'{name} {cls}: {len(matches)} detections')
  m=matches[0];inter=max(0,min(x1,m.x+m.width)-max(x0,m.x))*max(0,min(y1,m.y+m.height)-max(y0,m.y))
  iou=inter/((x1-x0)*(y1-y0)+m.width*m.height-inter)
  if iou<.8 or not x0<m.center[0]<x1 or not y0<m.center[1]<y1:raise ValueError(f'{name} {cls}: {iou}')
  items.append({'class':cls,'score':m.score,'iou':iou,'center':m.center})
 report[name]=items
negative=read_image(ROOT/'captures/chat-invite-20261014/01-guild-joined-invite.jpg')[31:931,1:1601]
found=detector.detect(negative,[.33,0,.67,1])
report['joined_negative']=[{'class':d.name,'score':d.match.score} for d in found if d.name.startswith('merchant')]
if any(d.name=='merchant_join' for d in found):raise ValueError('Joined card incorrectly detected as actionable')
(ROOT/'yolo-output/merchant-verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report,indent=2))
