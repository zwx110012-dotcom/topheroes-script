"""Replay saved screenshots only. Never capture a live window or send input."""
import json
from pathlib import Path
from crystal_gather import find_matches
from top_heroes import load_template
from yolo_detector import YoloDetector, QUEUE_ROI
from queue_monitor import QueueReader

ROOT = Path(__file__).resolve().parent
MODEL = ROOT / 'models/topheroes.onnx'
detector = YoloDetector(MODEL)
reader = QueueReader(ROOT, load_template(ROOT/'templates/idle.png'), detector, 4)
template = load_template(ROOT/'templates/carriage/return_city.png')
records=[]
for path in sorted((ROOT/'tests/fixtures/occlusion_20261019').glob('*.png')):
    frame=load_template(path)
    detections=detector.detect(frame, QUEUE_ROI)
    home=find_matches(frame,template,[1],0,[.9,.85,1,1])
    rows=reader.read(frame)
    record={'file':path.name,'old_home_score':home[0].score if home else None,
            'anchors':[{'name':d.name,'score':d.match.score,'box':[d.match.x,d.match.y,d.match.width,d.match.height]}
                       for d in detections if d.name in ('idle','timer_region')],
            'rows':None if rows is None else [r.__dict__ for r in rows]}
    records.append(record)
    print(json.dumps(record,ensure_ascii=False),flush=True)
(ROOT.parent/'故障截图回放.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
