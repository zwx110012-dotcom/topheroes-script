"""Explicit live launch verification; normal helper startup never runs this."""
import ctypes
import json
from pathlib import Path
import sys
import time

from game_launcher import game_is_running
from steam_restart import SteamRestartGuard


def run(output):
    result = {'ok':False,'frozen':bool(getattr(sys,'frozen',False)),
              'gathering_started':False,'logs':[]}
    guard = SteamRestartGuard(result['logs'].append)
    def check_stop():
        from crystal_gather import RunStopped
        if ctypes.windll.user32.GetAsyncKeyState(0x77) & 0x8000:
            raise RunStopped('F8')
    try:
        deadline = time.monotonic()+50
        while time.monotonic()<deadline:
            guard.poll(check_stop)
            if guard.disabled:
                break
            if guard.attempts and game_is_running():
                result.update(ok=True,dialog_closed=True,steam_launch_requested=True,
                              game_process_detected=True,attempts=guard.attempts)
                break
            time.sleep(.2)
    except Exception as error:
        result['error']=str(error)
    finally:
        Path(output).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if result['ok'] else 1
