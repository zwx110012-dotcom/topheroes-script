"""Offline-only packaged dialog verification; no game window or input APIs."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

from connection_recovery import DisconnectDialog, GuardedSession, Disconnected
from top_heroes import load_template


def run(resources, screenshots, output):
    output = Path(output)
    try:
        dialog = DisconnectDialog(resources)
        checks = []
        for path in sorted(Path(screenshots).glob('*.png')):
            frame = load_template(path)
            match = dialog.match(frame)
            if match is None or match.center != (800,537) or match.score < .99:
                raise AssertionError('无法确认真实断线弹窗：'+path.name)
            guard = GuardedSession(SimpleNamespace(capture=lambda:frame), dialog)
            try:
                guard.capture()
            except Disconnected:
                pass
            else:
                raise AssertionError('断线画面未阻断采集识别')
            negative = frame.copy()
            negative[430:459,670:929] = 114
            if dialog.match(negative) is not None:
                raise AssertionError('仅确认按钮不应触发重启')
            checks.append(dict(file=path.name,score=match.score,point=match.center,guarded=True))
        if len(checks)!=3:
            raise AssertionError('需要三张真实断线截图')
        result = dict(ok=True,frozen=bool(getattr(sys,'frozen',False)),checks=checks,clicked=False)
    except Exception as exc:
        result = dict(ok=False,error=str(exc),clicked=False)
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if result['ok'] else 1
