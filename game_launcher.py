"""Launch through Steam so the user's configured game proxy stays in effect."""
import csv
import io
import os
import subprocess

from top_heroes import WorkflowError, game_windows

STEAM_URI = 'steam://rungameid/4007740'
GAME_EXE = 'TopHeroes_Steam.exe'


def game_is_running():
    if game_windows():
        return True
    # The process may exist before Unity creates its game window. Detect that
    # interval too, so another click cannot launch a second proxy/game process.
    return image_is_running(GAME_EXE)


def image_is_running(name):
    try:
        result = subprocess.run(['tasklist.exe','/FI',f'IMAGENAME eq {name}','/FO','CSV','/NH'],
                                capture_output=True,text=True,encoding='mbcs',errors='replace',
                                timeout=5,creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError,subprocess.TimeoutExpired) as exc:
        raise WorkflowError('无法检查游戏是否已运行，请稍后重试') from exc
    if result.returncode:
        raise WorkflowError('无法检查游戏是否已运行，请稍后重试')
    return any(row and row[0].casefold()==name.casefold()
               for row in csv.reader(io.StringIO(result.stdout)))


def request_launch():
    if game_is_running():
        return False
    try:
        os.startfile(STEAM_URI)
    except OSError as exc:
        raise WorkflowError('无法通过 Steam 启动游戏，请确认 Steam 已安装并可正常打开') from exc
    return True
