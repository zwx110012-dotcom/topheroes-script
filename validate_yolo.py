"""Evaluate held-out visual annotations and the actual queue-reading pipeline."""
from pathlib import Path
import argparse
import json
import time

import numpy as np

from yolo_dataset import read_image, load_boxes
from yolo_detector import YoloDetector, NAVIGATION_ROIS
from queue_monitor import QueueReader
from top_heroes import load_template


def iou(a,b):
    x0,y0=max(a[0],b[0]),max(a[1],b[1]);x1,y1=min(a[2],b[2]),min(a[3],b[3])
    inter=max(0,x1-x0)*max(0,y1-y0)
    return inter/max(1,(a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-inter)


def evaluate(model, dataset):
    detector=YoloDetector(model,0.65)
    manifest=json.loads((dataset/'review.json').read_text(encoding='utf-8'))
    root=Path(__file__).resolve().parent
    readers={count:QueueReader(root,load_template(root/'templates/idle.png'),detector,count) for count in (3,4)}
    reports=[]
    for name,meta in manifest.items():
        if not meta.get('reviewed') or meta.get('split') not in ('test','val'):
            continue
        frame=read_image(dataset/'images'/name);h,w=frame.shape[:2]
        expected=load_boxes(dataset/'labels'/(Path(name).stem+'.txt'),w,h)
        queue_crop=name.startswith('queue_')
        if queue_crop:
            padded=np.zeros((900,1600,3),np.uint8);padded[:h,:w]=frame;frame=padded
        start=time.perf_counter()
        detections=detector.detect(frame)
        if frame.shape[1] >= 1000:
            detections=[d for d in detections if d.name not in NAVIGATION_ROIS]
            for kind,roi in NAVIGATION_ROIS.items():
                detections.extend(d for d in detector.detect(frame,roi) if d.name==kind)
        if any(b[0].startswith('queue_') or b[0] in ('idle','timer_region') for b in expected):
            queue=detector.detect(frame,[0,0.12,0.2,0.45])
            detections=[d for d in detections if not d.name.startswith('queue_') and d.name not in ('idle','timer_region')]+queue
        matches=[];used=set()
        for kind,x0,y0,x1,y1 in expected:
            options=[(iou((x0,y0,x1,y1),(d.match.x,d.match.y,d.match.x+d.match.width,d.match.y+d.match.height)),i,d)
                     for i,d in enumerate(detections) if d.name==kind and i not in used]
            best=max(options,key=lambda item:item[0],default=None)
            success=bool(best and best[0]>=0.5)
            if success:used.add(best[1])
            matches.append({'class':kind,'ok':success,'iou':round(best[0],3) if best else 0,
                            'center_inside':bool(best and x0<=best[2].match.center[0]<=x1 and y0<=best[2].match.center[1]<=y1)})
        reader=readers[meta.get('queue_capacity',3)]
        rows=reader.read(frame) if any(b[0]=='queue_header' for b in expected) else None
        reports.append({'image':name,'split':meta['split'],'milliseconds':round((time.perf_counter()-start)*1000),
                        'matches':matches,'unmatched_predictions':[d.name for i,d in enumerate(detections) if i not in used],
                        'queues':[{'state':r.state,'seconds':r.seconds,'text':r.text} for r in rows] if rows else None})
    return reports


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--model',type=Path,required=True)
    p.add_argument('--dataset',type=Path,default=Path('datasets/topheroes'))
    p.add_argument('--output',type=Path,default=Path('yolo-output/validation.json'))
    a=p.parse_args();result=evaluate(a.model,a.dataset)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    for item in result:
        missed=[m['class'] for m in item['matches'] if not m['ok']]
        print(item['image'],'missed=',missed,'extra=',item['unmatched_predictions'],'queues=',item['queues'])
