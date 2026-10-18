"""Visible-window template matching helper for Top Heroes on Windows."""

from __future__ import annotations

import argparse
import ctypes
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import native_runtime  # Must precede native CV/ONNX imports on old Anaconda runtimes.
import cv2
import numpy as np
import win32con
import win32api
import win32gui
import yaml
from PIL import ImageGrab

try:
    if not ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
        ctypes.windll.user32.SetProcessDPIAware()
except (AttributeError, OSError):
    pass


class WorkflowError(Exception):
    pass


class WindowChanged(WorkflowError):
    """Window geometry changed before mouse-down; no click was issued."""
    pass


@dataclass(frozen=True)
class Match:
    score: float
    x: int
    y: int
    width: int
    height: int

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.width // 2, self.y + self.height // 2


def windows() -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []

    def collect(hwnd: int, _: Any) -> None:
        if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd):
            found.append((hwnd, win32gui.GetWindowText(hwnd)))

    win32gui.EnumWindows(collect, None)
    return found


def find_window(title: str) -> int:
    hits = [(hwnd, name) for hwnd, name in windows() if title.casefold() in name.casefold()]
    if len(hits) != 1:
        raise WorkflowError(f"窗口标题 {title!r} 匹配到 {len(hits)} 个窗口；请用 windows 查看并写得更具体")
    return hits[0][0]


def normalized_title(title: str) -> str:
    return "".join(char for char in title.casefold() if char.isalnum())


def game_windows() -> list[tuple[int, str]]:
    """Select the Steam game title, excluding this helper and unrelated windows."""
    return [(hwnd, title) for hwnd, title in windows()
            if normalized_title(title) == "steamtopheroes"]


def find_game_window(title: str) -> int:
    query = normalized_title(title)
    if not query:
        raise WorkflowError("请选择 Steam Top Heroes 游戏窗口")
    hits = [(hwnd, name) for hwnd, name in game_windows()
            if query in normalized_title(name)]
    if len(hits) != 1:
        raise WorkflowError(f"{title!r} 匹配到 {len(hits)} 个 Steam Top Heroes 游戏窗口；请打开游戏并刷新窗口")
    return hits[0][0]


def client_box(hwnd: int) -> tuple[int, int, int, int]:
    if win32gui.IsIconic(hwnd):
        raise WorkflowError("游戏窗口已最小化")
    left, top, right, bottom = win32gui.GetClientRect(hwnd)
    x1, y1 = win32gui.ClientToScreen(hwnd, (left, top))
    x2, y2 = win32gui.ClientToScreen(hwnd, (right, bottom))
    if x2 <= x1 or y2 <= y1:
        raise WorkflowError("无法读取游戏窗口客户区")
    return x1, y1, x2, y2


def require_foreground(hwnd: int) -> None:
    if win32gui.GetForegroundWindow() != hwnd:
        raise WorkflowError("目标窗口不在前台；请先切换到游戏再截图或运行")


def resize_client(hwnd: int, size: tuple[int, int]) -> None:
    """Resize the client area using only Windows window geometry."""
    require_foreground(hwnd)
    if win32gui.GetWindowPlacement(hwnd)[1] == win32con.SW_SHOWMAXIMIZED:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    box = client_box(hwnd)
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width = size[0] + (right - left) - (box[2] - box[0])
    height = size[1] + (bottom - top) - (box[3] - box[1])
    monitor = win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST)
    x0, y0, x1, y1 = win32api.GetMonitorInfo(monitor)["Work"]
    if width > x1 - x0 or height > y1 - y0:
        raise WorkflowError(f"屏幕可用区域不足以完整显示 {size[0]}×{size[1]} 游戏窗口")
    x, y = min(max(left, x0), x1 - width), min(max(top, y0), y1 - height)
    require_foreground(hwnd)
    win32gui.SetWindowPos(hwnd, 0, x, y, width, height,
                          win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE)


def screenshot(hwnd: int) -> np.ndarray:
    require_foreground(hwnd)
    image = ImageGrab.grab(bbox=client_box(hwnd), all_screens=True)
    return cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)


def park_mouse(hwnd: int, size: tuple[int, int]) -> bool:
    """Move the game's software cursor away from controls before recognition."""
    require_foreground(hwnd)
    box = client_box(hwnd)
    if (box[2] - box[0], box[3] - box[1]) != size:
        raise WindowChanged("截图前窗口尺寸改变")
    cursor = POINT()
    if not ctypes.windll.user32.GetCursorPos(ctypes.byref(cursor)):
        raise WorkflowError("无法确认鼠标位置，已停止")
    if cursor.x <= 2 and cursor.y <= 2:
        raise WorkflowError("鼠标位于屏幕左上角，已中止")
    x, y = box[0] + size[0] // 2, box[1] + 80
    if (cursor.x, cursor.y) == (x, y):
        return False
    if not ctypes.windll.user32.SetCursorPos(x, y):
        raise WorkflowError("无法移开游戏鼠标指针，已停止")
    return True


def load_template(path: Path) -> np.ndarray:
    if not path.is_file():
        raise WorkflowError(f"模板不存在：{path}")
    image = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None or min(image.shape[:2]) < 8:
        raise WorkflowError(f"模板无法读取或小于 8 像素：{path}")
    if float(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).std()) < 5:
        raise WorkflowError(f"模板纹理太少，容易误判：{path}")
    return image


def locate(image: np.ndarray, template: np.ndarray, roi: list[int] | None = None) -> Match:
    height, width = image.shape[:2]
    if roi is None:
        x0, y0, x1, y1 = 0, 0, width, height
    else:
        if len(roi) != 4 or not all(isinstance(v, int) for v in roi):
            raise WorkflowError("roi 必须是 [x1, y1, x2, y2] 四个整数")
        x0, y0, x1, y1 = roi
        if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
            raise WorkflowError(f"roi {roi} 超出了窗口客户区 {width}x{height}")
    search = image[y0:y1, x0:x1]
    th, tw = template.shape[:2]
    if th > search.shape[0] or tw > search.shape[1]:
        raise WorkflowError("模板大于搜索区域")
    result = cv2.matchTemplate(search, template, cv2.TM_CCOEFF_NORMED)
    _, score, _, (x, y) = cv2.minMaxLoc(result)
    if not np.isfinite(score):
        raise WorkflowError("模板匹配得分无效；请换一张有明显纹理的模板")
    return Match(float(score), x + x0, y + y0, tw, th)


def read_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as stream:
        config = yaml.safe_load(stream)
    if not isinstance(config, dict) or not isinstance(config.get("window_title"), str):
        raise WorkflowError("配置需要 window_title 字符串")
    steps = config.get("steps")
    if not isinstance(steps, list) or not steps:
        raise WorkflowError("配置需要非空 steps 列表")
    for index, step in enumerate(steps, 1):
        if not isinstance(step, dict) or not isinstance(step.get("target"), str):
            raise WorkflowError(f"第 {index} 步缺少 target")
        after = step.get("after")
        if not isinstance(after, dict) or not isinstance(after.get("template"), str):
            raise WorkflowError(f"第 {index} 步缺少 after.template 视觉确认")
        for key in ("target",):
            load_template(path.parent / step[key])
        load_template(path.parent / after["template"])
    confidence = float(config.get("confidence", 0.88))
    if not 0.5 <= confidence <= 1:
        raise WorkflowError("confidence 必须在 0.5 到 1 之间")
    config["confidence"] = confidence
    config["poll_seconds"] = max(0.1, float(config.get("poll_seconds", 0.5)))
    return config


def wait_match(hwnd: int, template: np.ndarray, threshold: float, timeout: float,
               poll: float, roi: list[int] | None = None) -> Match:
    deadline = time.monotonic() + timeout
    best: Match | None = None
    while True:
        result = locate(screenshot(hwnd), template, roi)
        if best is None or result.score > best.score:
            best = result
        if result.score >= threshold:
            return result
        if time.monotonic() >= deadline:
            assert best is not None
            raise WorkflowError(f"超时：最高匹配分数 {best.score:.3f}，要求 {threshold:.3f}")
        time.sleep(poll)


def click(hwnd: int, point: tuple[int, int], initial_size: tuple[int, int]) -> None:
    box = client_box(hwnd)
    size = (box[2] - box[0], box[3] - box[1])
    if size != initial_size:
        raise WindowChanged(f"窗口尺寸从 {initial_size} 变为 {size}，本次未点击")
    if win32gui.GetForegroundWindow() != hwnd:
        raise WorkflowError("游戏窗口不在前台，已停止点击")
    x, y = box[0] + point[0], box[1] + point[1]
    if not (box[0] <= x < box[2] and box[1] <= y < box[3]):
        raise WorkflowError("点击位置超出游戏窗口")
    if ctypes.windll.user32.GetCursorPos(ctypes.byref(_cursor := POINT())):
        if _cursor.x <= 2 and _cursor.y <= 2:
            raise WorkflowError("鼠标位于屏幕左上角，已中止")
    if not ctypes.windll.user32.SetCursorPos(x, y):
        raise WorkflowError("鼠标定位失败；停止点击")
    require_foreground(hwnd)
    if client_box(hwnd) != box:
        raise WindowChanged("鼠标定位期间窗口改变，本次未点击")
    ctypes.windll.user32.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    try:
        time.sleep(0.05)
    finally:
        ctypes.windll.user32.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def run(config_path: Path, execute: bool) -> None:
    config = read_config(config_path)
    hwnd = find_window(config["window_title"])
    box = client_box(hwnd)
    size = (box[2] - box[0], box[3] - box[1])
    mode = "执行" if execute else "预览"
    print(f"{mode}：{config['window_title']}，客户区 {size[0]}x{size[1]}")
    if not execute:
        image = screenshot(hwnd)
        for number, step in enumerate(config["steps"], 1):
            target = load_template(config_path.parent / step["target"])
            match = locate(image, target, step.get("roi"))
            threshold = float(step.get("confidence", config["confidence"]))
            print(f"[{number}] {step.get('name', number)}: score={match.score:.3f} "
                  f"center={match.center} {'匹配' if match.score >= threshold else '未匹配'}")
        print("预览完成；没有点击。后续页面的按钮需进入对应页面后再预览。")
        return
    for number, step in enumerate(config["steps"], 1):
        name = str(step.get("name", number))
        threshold = float(step.get("confidence", config["confidence"]))
        target = load_template(config_path.parent / step["target"])
        match = wait_match(hwnd, target, threshold,
                           float(step.get("timeout_seconds", 15)),
                           config["poll_seconds"], step.get("roi"))
        print(f"[{number}] {name}: target={match.score:.3f} center={match.center}")
        after = step["after"]
        confirm = load_template(config_path.parent / after["template"])
        after_threshold = float(after.get("confidence", threshold))
        before = locate(screenshot(hwnd), confirm, after.get("roi"))
        if before.score >= after_threshold:
            raise WorkflowError(f"第 {number} 步的确认画面在点击前已出现，无法验证点击效果")
        click(hwnd, match.center, size)
        verified = wait_match(hwnd, confirm, after_threshold,
                              float(after.get("timeout_seconds", 8)),
                              config["poll_seconds"], after.get("roi"))
        print(f"    confirmed={verified.score:.3f}")
    print("流程完成；每步均通过画面确认。")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("windows", help="列出可见窗口")
    capture = commands.add_parser("capture", help="截取窗口客户区")
    capture.add_argument("--title", required=True)
    capture.add_argument("--output", type=Path, required=True)
    check = commands.add_parser("check", help="检查流程文件和模板")
    check.add_argument("--config", type=Path, required=True)
    workflow = commands.add_parser("run", help="预览或执行视觉流程")
    workflow.add_argument("--config", type=Path, required=True)
    workflow.add_argument("--execute", action="store_true", help="允许鼠标点击")
    gather_command = commands.add_parser("gather", help="6级红水晶采集，可持续监测返程并补队")
    gather_command.add_argument("--config", type=Path, default=Path(__file__).parent / "gather_crystal.yaml")
    gather_command.add_argument("--execute", action="store_true", help="允许鼠标点击")
    gather_command.add_argument("--start-delay", type=float, default=5, help="开始前切换到游戏的等待秒数")
    args = parser.parse_args()
    try:
        if args.command == "windows":
            for hwnd, title in windows():
                encoding = sys.stdout.encoding or "utf-8"
                safe_title = title.encode(encoding, errors="backslashreplace").decode(encoding)
                print(f"{hwnd:>10}  {safe_title}")
        elif args.command == "capture":
            image = screenshot(find_window(args.title))
            args.output.parent.mkdir(parents=True, exist_ok=True)
            extension = args.output.suffix.lower()
            if extension not in (".png", ".jpg", ".jpeg"):
                raise WorkflowError("截图文件名需要 .png 或 .jpg 后缀")
            encoded, buffer = cv2.imencode(extension, image)
            if not encoded:
                raise WorkflowError(f"截图保存失败：{args.output}")
            buffer.tofile(str(args.output))
            print(f"已保存 {args.output} ({image.shape[1]}x{image.shape[0]})")
        elif args.command == "check":
            config = read_config(args.config)
            print(f"配置有效：{len(config['steps'])} 步；所有模板可读取")
        elif args.command == "run":
            run(args.config, args.execute)
        elif args.command == "gather":
            from crystal_gather import gather
            gather(args.config, args.execute, args.start_delay)
        return 0
    except (WorkflowError, OSError, ValueError, yaml.YAMLError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("已中止", file=sys.stderr)
        return 130


if __name__ == "__main__":
    # Share exception and data classes with the specialized workflow module.
    sys.modules["top_heroes"] = sys.modules[__name__]
    raise SystemExit(main())
