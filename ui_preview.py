"""Capture this application's rendered preview without starting the game."""
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

from PIL import ImageGrab
from ui_smoke import check_layout


def run(app,output):
    output=Path(output)
    output.mkdir(parents=True,exist_ok=True)
    result={'ok':False,'frozen':bool(getattr(sys,'frozen',False)),'game_started':False}
    try:
        layout=check_layout(app)
        with patch('game_launcher.request_launch',return_value=False) as request, \
             patch('steam_restart.SteamRestartGuard') as guard:
            guard.return_value.pending=False
            app.launch_game()
            app.launch_game()
            deadline=time.monotonic()+3
            while app.launching_game and time.monotonic()<deadline:
                app.root.update()
                time.sleep(.02)
            assert not app.launching_game, 'Launch feedback did not arrive'
            assert request.call_count==1, 'Repeated launch click was not suppressed'
            assert not app.busy and app.worker is None, 'Launching game started gathering'
            assert str(app.launch_button.cget('state'))=='normal'
        app.status_var.set('等待开始')
        app.detail_var.set('先点击“启动游戏”，加载完成后再点击“启动采集”。')
        app.root.title('Top Heroes · 日常助手 · 界面预览')
        app.size_var.set('未连接')
        app.add_log('已就绪：游戏状态只从截图识别，动作使用鼠标点击。')
        app.root.update()
        screenshots=[]
        for size in ('1160x800','1000x700'):
            app.root.geometry(size+'+60+40')
            app.root.update()
            app.root.lift()
            app.root.update_idletasks()
            time.sleep(.2)
            target=output/('界面预览_'+size+'.png')
            image=ImageGrab.grab(window=app.root.winfo_id())
            if image.width<900 or image.height<600:
                raise AssertionError('Preview window capture size is invalid')
            image.save(target)
            screenshots.append(str(target))
        result.update(ok=True,layout=layout,screenshots=screenshots,
                      duplicate_click_blocked=True,does_not_start_gathering=True)
    except Exception as exc:
        result['error']=str(exc)
        raise
    finally:
        (output/'界面验证.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
