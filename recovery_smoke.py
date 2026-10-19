"""Packaged, offline-only checks against archived obstruction screenshots."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

from queue_monitor import QueueReader
from top_heroes import load_template
from visual_navigation import VisualNavigator
from yolo_detector import YoloDetector


def run(resources, model, screenshots, output):
    output=Path(output)
    try:
        resources=Path(resources)
        detector=YoloDetector(Path(model))
        detector.require_verified()
        detector.require_capacity(4)
        reader=QueueReader(resources,load_template(resources/'templates/idle.png'),detector,4)
        navigation=VisualNavigator(SimpleNamespace(base=resources))
        checks=[]
        for path in sorted(Path(screenshots).glob('*.png')):
            frame=load_template(path)
            home=navigation.match('return_city',frame)
            rows=reader.read(frame)
            if home is None:
                raise AssertionError('无法识别故障画面中的回城按钮：'+path.name)
            if rows is not None:
                raise AssertionError('遮挡画面不应授权派队：'+path.name)
            checks.append({'file':path.name,'return_city_score':home.score,
                           'return_city_point':home.center,'refill_authorized':False})
        if len(checks)!=5:
            raise AssertionError('需要本次保存的五张故障截图')
        result={'ok':True,'frozen':bool(getattr(sys,'frozen',False)),
                'providers':detector.session.get_providers(),'checks':checks,'clicked':False}
    except Exception as exc:
        result={'ok':False,'error':str(exc),'clicked':False}
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if result['ok'] else 1
