"""Red crystal gathering: pixels in, mouse clicks out."""

from __future__ import annotations

import time
import threading
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np
import yaml

from top_heroes import (Match, WorkflowError, WindowChanged, client_box, click,
                        find_game_window as find_window, load_template, screenshot, resize_client, require_foreground, park_mouse)
from queue_monitor import QueueReader, QueueRow, next_check_seconds


class RunStopped(WorkflowError):
    pass


class NoIdleQueues(WorkflowError):
    pass


class TemplateTimeout(WorkflowError):
    def __init__(self, name: str, frame: np.ndarray, message: str):
        super().__init__(message)
        self.name, self.frame = name, frame


class SearchUnavailable(WorkflowError):
    """Search returned to a verified world screen without a gathering button."""
    pass


class QueueUnavailable(WorkflowError):
    """No stable queue observation; waiting is allowed, mouse input is not."""
    pass


class DepartureUncertain(WorkflowError):
    def __init__(self, message, old_idle, idle_rows):
        super().__init__(message)
        self.old_idle,self.idle_rows=old_idle,idle_rows


def find_matches(frame: np.ndarray, template: np.ndarray, scales: list[float],
                 threshold: float, roi: list[float] | None = None,
                 blur_sigma: float = 0) -> list[Match]:
    """Multi-scale normalized correlation with nonmaximum suppression."""
    height, width = frame.shape[:2]
    roi = roi or [0.0, 0.0, 1.0, 1.0]
    x0, y0, x1, y1 = [round(v * d) for v, d in zip(roi, [width, height, width, height])]
    if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
        raise WorkflowError(f"无效的相对搜索区域：{roi}")
    search = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
    if blur_sigma:
        search = cv2.GaussianBlur(search, (5, 5), blur_sigma)
    candidates = []
    for scale in scales:
        tw, th = max(8, round(gray.shape[1] * scale)), max(8, round(gray.shape[0] * scale))
        if tw > search.shape[1] or th > search.shape[0]:
            continue
        resized = cv2.resize(gray, (tw, th), interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)
        if blur_sigma:
            resized = cv2.GaussianBlur(resized, (5, 5), blur_sigma)
        response = cv2.matchTemplate(search, resized, cv2.TM_CCOEFF_NORMED)
        # Extract separated peaks; do not collect every pixel around one match.
        for _ in range(6):
            _, score, _, (x, y) = cv2.minMaxLoc(response)
            if not np.isfinite(score) or score < threshold:
                break
            candidates.append(Match(float(score), x + x0, y + y0, tw, th))
            response[max(0, y - th // 2):y + th // 2 + 1,
                     max(0, x - tw // 2):x + tw // 2 + 1] = -1
    found: list[Match] = []
    for candidate in sorted(candidates, key=lambda m: m.score, reverse=True):
        cx, cy = candidate.center
        if not any(abs(cx - other.center[0]) < max(candidate.width, other.width) * 0.65
                   and abs(cy - other.center[1]) < max(candidate.height, other.height) * 0.65
                   for other in found):
            found.append(candidate)
    return found


def read_gather_config(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        config = yaml.safe_load(stream)
    if not isinstance(config, dict) or not isinstance(config.get("window_title"), str):
        raise WorkflowError("采集配置需要 window_title")
    required = {"world", "search_icon", "resources", "red_crystal", "minus", "plus",
                "level_six", "search_button", "gather", "depart", "idle"}
    specs = config.get("templates", {})
    if not isinstance(specs, dict):
        raise WorkflowError("templates 必须是模板名称与配置的映射")
    if required - specs.keys():
        raise WorkflowError(f"采集配置缺少模板：{sorted(required - specs.keys())}")
    for name in required:
        spec = specs[name]
        if not isinstance(spec, dict) or not isinstance(spec.get("path"), str):
            raise WorkflowError(f"模板 {name} 缺少 path")
        load_template(path.parent / spec["path"])
        confidence = float(spec.get("confidence", config.get("confidence", 0.9)))
        if not 0.5 <= confidence <= 1:
            raise WorkflowError(f"模板 {name} confidence 无效")
    if not isinstance(config.get("scales"), list) or not config["scales"]:
        raise WorkflowError("采集配置需要 scales")
    if any(not isinstance(s, (int, float)) or not 0.3 <= s <= 2 for s in config["scales"]):
        raise WorkflowError("scales 必须在 0.3 到 2 之间")
    capacity = config.get('queue_capacity',3)
    if type(capacity) is not int or capacity not in (3,4):
        raise WorkflowError('queue_capacity 只能是3或4')
    config['queue_capacity'] = capacity
    if not 1 <= int(config.get("dispatches", capacity)) <= capacity:
        raise WorkflowError(f"每个大循环的 dispatches 只能是 1 到 {capacity}")
    size = config.get("window_size", [1600, 900])
    if not isinstance(size, list) or len(size) != 2 or any(type(v) is not int or v < 400 for v in size):
        raise WorkflowError("window_size 必须是两个至少400像素的整数")
    config["window_size"] = size
    target_level=config.get('target_level',7)
    if type(target_level) is not int or target_level not in available_levels(path.parent):
        raise WorkflowError('target_level 必须是已有图像模板的矿点等级')
    config['target_level']=target_level
    if type(config.get("continuous", True)) is not bool:
        raise WorkflowError("continuous 必须为 true 或 false")
    if type(config.get('chat_invites',False)) is not bool:
        raise WorkflowError('chat_invites 必须为 true 或 false')
    for key, default, lower, upper in [("monitor_max_seconds", 30, 1, 60),
                                       ("monitor_fallback_seconds", 2, 0.5, 10)]:
        value = float(config.get(key, default))
        if not lower <= value <= upper:
            raise WorkflowError(f"{key} 必须在 {lower} 到 {upper} 秒之间")
        config[key] = value
    return config


def available_levels(base: Path) -> list[int]:
    levels={6} if (base/'templates/level_six.png').is_file() else set()
    for path in (base/'templates/levels').glob('level_*.png'):
        suffix=path.stem.removeprefix('level_')
        if suffix.isdigit() and 1<=int(suffix)<=15:
            levels.add(int(suffix))
    return sorted(levels)


class GameSession:
    def __init__(self, title: str, on_frame: Callable | None = None,
                 target_size: tuple[int, int] = (1600, 900),
                 stop_event: threading.Event | None = None,
                 logger: Callable[[str], None] | None = None):
        self.hwnd = find_window(title)
        self.on_frame = on_frame
        self.size = tuple(target_size)
        self.stop_event, self.logger = stop_event, logger
        self.generation = 0
        self.capture_box = None
        self.ensure_size()

    def sleep(self, seconds: float) -> None:
        if self.stop_event:
            if self.stop_event.wait(seconds):
                raise RunStopped("已停止；取消窗口恢复")
        else:
            time.sleep(seconds)

    def ensure_size(self) -> bool:
        if self.stop_event and self.stop_event.is_set():
            raise RunStopped("已停止；取消窗口恢复")
        require_foreground(self.hwnd)
        box = client_box(self.hwnd)
        current = (box[2] - box[0], box[3] - box[1])
        if current == self.size:
            return False
        self.generation += 1
        if self.logger:
            self.logger(f"窗口尺寸 {current[0]}×{current[1]} → {self.size[0]}×{self.size[1]}，暂停点击并恢复")
        for _ in range(3):
            resize_client(self.hwnd, self.size)
            # Allow the game to render its new layout; all waits honor F8.
            self.sleep(0.6)
            stable = True
            for _ in range(3):
                require_foreground(self.hwnd)
                box = client_box(self.hwnd)
                if (box[2] - box[0], box[3] - box[1]) != self.size:
                    stable = False
                    break
                self.sleep(0.15)
            if stable:
                if self.logger:
                    self.logger("窗口已恢复并稳定；使用新截图继续识别")
                return True
        raise WorkflowError("窗口连续改变或拒绝指定尺寸，已停止点击")

    def capture(self) -> np.ndarray:
        for _ in range(3):
            self.ensure_size()
            try:
                if park_mouse(self.hwnd, self.size):
                    self.sleep(0.12)
                before = client_box(self.hwnd)
                frame = screenshot(self.hwnd)
                after = client_box(self.hwnd)
            except WindowChanged:
                continue
            if before == after and (frame.shape[1], frame.shape[0]) == self.size:
                self.capture_box = after
                break
        else:
            raise WorkflowError("截图期间窗口持续改变，已停止点击")
        if self.on_frame:
            self.on_frame(frame)
        return frame

    def click(self, point: tuple[int, int]) -> None:
        if self.ensure_size():
            raise WindowChanged("已恢复窗口，需重新识别目标后点击")
        if self.capture_box is not None and client_box(self.hwnd) != self.capture_box:
            self.generation += 1
            raise WindowChanged("识别后窗口位置改变，需重新截图定位")
        try:
            click(self.hwnd, point, self.size)
        except WindowChanged:
            self.ensure_size()
            raise


class CrystalGatherer:
    def __init__(self, config: dict[str, Any], base: Path, session: Any,
                 stop_event: threading.Event | None = None,
                 logger: Callable[[str], None] | None = None,
                 on_event: Callable | None = None):
        self.config, self.session = config, session
        self.specs = config["templates"]
        self.templates = {name: load_template(base / spec["path"]) for name, spec in self.specs.items()}
        self.scales = config["scales"]
        self.poll = float(config.get("poll_seconds", 0.25))
        self.timeout = float(config.get("timeout_seconds", 12))
        self.stop_event = stop_event
        self.logger = logger
        self.on_event = on_event
        self.completed = 0
        self.capacity = int(config.get('queue_capacity',3))
        self.queue_reader: QueueReader | None = None
        self.base = base
        self.level_templates={n:load_template(base/'templates/level_six.png' if n==6 else
            base/'templates/levels'/f'level_{n}.png') for n in available_levels(base)}
        self.detector = None
        if config.get("recognition", "template") == "yolo":
            from yolo_detector import YoloDetector
            self.detector = YoloDetector(Path(config["yolo_model"]), config.get("yolo_confidence", 0.65))
            self.log(f"识别模型：{self.detector.path.name}（ONNX / CPU）")

    def require_ready(self):
        if self.detector:
            self.detector.require_verified()
            self.detector.require_capacity(self.capacity)

    def check_stop(self) -> None:
        if self.stop_event and self.stop_event.is_set():
            raise RunStopped("已停止；不会继续点击")

    def sleep(self, seconds: float) -> None:
        self.check_stop()
        if self.stop_event:
            if self.stop_event.wait(seconds):
                self.check_stop()
        else:
            time.sleep(seconds)

    def log(self, message: str) -> None:
        if self.logger:
            self.logger(message)
        else:
            print(message, flush=True)

    def emit(self, kind: str, **data: Any) -> None:
        if self.on_event:
            self.on_event(kind, data)

    def matches(self, name: str, frame: np.ndarray | None = None,
                roi: list[float] | None = None) -> list[Match]:
        spec = self.specs[name]
        self.check_stop()
        if frame is None:
            frame = self.session.capture()
        if self.detector:
            found = self.detector.matches(name, frame, roi if roi is not None else spec.get("roi"))
            if not found and name in ('world','search_icon'):
                found=find_matches(frame,self.templates[name],self.scales,.90,
                                   roi if roi is not None else spec.get('roi'))
            self.check_stop()
            return found
        found = find_matches(frame, self.templates[name], self.scales,
                            float(spec.get("confidence", self.config.get("confidence", 0.9))),
                            roi if roi is not None else spec.get("roi"),
                            float(spec.get("blur_sigma", 0)))
        self.check_stop()
        return found

    def ensure_world(self, reset=False):
        from visual_navigation import VisualNavigator
        if not hasattr(self,'navigator'):self.navigator=VisualNavigator(self)
        return self.navigator.ensure_world(reset=reset)

    def expand_collapsed_queues(self):
        from visual_navigation import VisualNavigator
        if not hasattr(self,'navigator'):self.navigator=VisualNavigator(self)
        frame=self.session.capture()
        if self.matches('search_icon',frame) and self.navigator.match('queue_collapsed',frame):
            self.ensure_world()
            return True
        return False

    def wait(self, name: str) -> Match:
        deadline = time.monotonic() + self.timeout
        stable = 0
        previous: Match | None = None
        generation = getattr(self.session, "generation", 0)
        while True:
            self.check_stop()
            frame = self.session.capture()
            found = self.matches(name, frame)
            current_generation = getattr(self.session, "generation", 0)
            if current_generation != generation:
                previous, stable = None, 0
                generation = current_generation
                deadline = time.monotonic() + self.timeout
            current = found[0] if found else None
            if current and previous and abs(current.x - previous.x) < 5 and abs(current.y - previous.y) < 5:
                stable += 1
            else:
                stable = 1 if current else 0
            if stable >= 2:
                assert current is not None
                return current
            previous = current
            if time.monotonic() >= deadline:
                spec = self.specs[name]
                best = (self.matches(name, frame) if self.detector else
                        find_matches(frame, self.templates[name], self.scales, 0,
                                     spec.get("roi"), float(spec.get("blur_sigma", 0))))
                match = best[0] if best else None
                threshold = (self.detector.confidence if self.detector else
                             float(spec.get("confidence", self.config.get("confidence", 0.9))))
                self.emit("diagnostic", frame=frame, name=name, match=match, threshold=threshold)
                score = f"最高得分 {match.score:.3f}，要求 {threshold:.3f}" if match else "搜索区域无可用匹配"
                raise TemplateTimeout(name, frame, f"等待 {name} 图像超时（{score}）；未点击")
            self.sleep(self.poll)

    def press(self, name: str) -> Match:
        self.require_ready()
        for _ in range(3):
            match = self.wait(name)
            self.emit("step", name=name, score=match.score)
            self.check_stop()
            try:
                self.session.click(match.center)
            except WindowChanged as exc:
                # This exception guarantees mouse-down did not happen.
                self.log(f"{exc}；重新识别 {name}")
                continue
            self.log(f"点击 {name}: score={match.score:.3f}, center={match.center}")
            self.sleep(float(self.config.get("click_settle_seconds", 0.35)))
            return match
        raise WorkflowError("点击前窗口反复改变，已停止")

    def idle_count(self, frame: np.ndarray | None = None) -> int:
        count = len(self.matches("idle", frame))
        if count > self.capacity:
            raise WorkflowError(f"空闲队列图像匹配超过{self.capacity}处；识别存在歧义")
        return count

    def stable_idle_count(self) -> int:
        deadline = time.monotonic() + self.timeout
        previous = None
        stable = 0
        while True:
            count = self.idle_count()
            stable = stable + 1 if count == previous else 1
            if stable >= 3:
                return count
            previous = count
            if time.monotonic() >= deadline:
                raise WorkflowError("空闲队列识别不稳定；未点击搜索")
            self.sleep(self.poll)

    def level_region(self, frame: np.ndarray, plus: Match) -> np.ndarray:
        # Geometry is relative to the recognized + button, never a screen coordinate.
        scale_x = plus.width / self.templates["plus"].shape[1]
        scale_y = plus.height / self.templates["plus"].shape[0]
        x0, y0 = plus.x + round(61 * scale_x), plus.y + round(6 * scale_y)
        x1, y1 = x0 + round(66 * scale_x), y0 + round(35 * scale_y)
        if not (0 <= x0 < x1 <= frame.shape[1] and 0 <= y0 < y1 <= frame.shape[0]):
            raise WorkflowError("无法定位等级标签")
        return frame[y0:y1, x0:x1]

    def is_six(self, frame: np.ndarray, plus: Match) -> bool:
        # A detection class cannot reliably distinguish tiny 6/7 glyphs after
        # full-frame downsampling. YOLO locates the + anchor; verify the actual
        # level text at original pixel resolution, like countdown recognition.
        region = self.level_region(frame, plus)
        spec = self.specs["level_six"]
        return bool(find_matches(region, self.templates["level_six"], self.scales,
                                 float(spec.get("confidence", 0.96)), [0, 0, 1, 1]))

    def read_level(self,frame,plus) -> int | None:
        region=self.level_region(frame,plus)
        scores=[]
        for level,template in self.level_templates.items():
            matches=find_matches(region,template,self.scales,.80,[0,0,1,1])
            if matches:scores.append((matches[0].score,level))
        scores.sort(reverse=True)
        if not scores or scores[0][0]<.96 or (len(scores)>1 and scores[0][0]-scores[1][0]<.01):
            return None
        return scores[0][1]

    def wait_level(self,plus,different_from=None) -> int:
        deadline=time.monotonic()+self.timeout
        previous=None
        generation=getattr(self.session,'generation',0)
        while True:
            self.check_stop()
            frame=self.session.capture()
            if getattr(self.session,'generation',0)!=generation:
                plus=self.wait('plus');generation=getattr(self.session,'generation',0)
                previous=None;deadline=time.monotonic()+self.timeout
                continue
            level=self.read_level(frame,plus)
            if level is not None and level!=different_from and level==previous:
                return level
            previous=level
            if time.monotonic()>=deadline:
                self.emit('diagnostic',frame=frame,name='resource_level',match=None,threshold=.96)
                raise TemplateTimeout('resource_level',frame,'矿点等级未从图像稳定确认；重新检查页面')
            self.sleep(self.poll)

    def set_level(self,level: int) -> None:
        if level not in self.level_templates:
            raise WorkflowError(f'没有{level}级图像模板；停止搜索')
        plus=self.wait('plus')
        current=self.wait_level(plus)
        for _ in range(int(self.config.get('max_level_adjustments',16))):
            if current==level:
                self.log(f'已从图像确认等级为{level}')
                return
            self.press('plus' if current<level else 'minus')
            plus=self.wait('plus')
            current=self.wait_level(plus,different_from=current)
        raise WorkflowError(f'等级调整达到上限，仍未确认{level}级；停止搜索')

    def set_level_six(self) -> None:
        self.set_level(6)

    def open_world_search(self) -> int:
        if not self.matches("search_icon"):
            self.ensure_world()
        self.wait("search_icon")
        count = self.stable_idle_count()
        self.log(f"搜索前图像确认：空闲 {count}/{self.capacity} 队")
        self.emit("queue", idle=count, capacity=self.capacity)
        if count == 0:
            raise NoIdleQueues("未识别到空闲队列；队列已满或列表被折叠，停止派队")
        self.press("search_icon")
        self.press("resources")
        self.press("red_crystal")
        self.set_level(self.config.get('target_level',7))
        return count

    def confirm_departure(self, old_idle: int, idle_rows: set[int] | None = None) -> None:
        deadline = time.monotonic() + self.timeout
        stable = 0
        observed = None
        while True:
            frame = self.session.capture()
            world = bool(self.matches("search_icon", frame)) and not self.matches("depart", frame)
            count = self.idle_count(frame) if world else None
            if count != observed:
                self.log(f"出发后图像识别：空闲 {count}/{self.capacity} 队，预期 {old_idle - 1}/{self.capacity}")
                observed = count
            if idle_rows is None:
                correct = world and count == old_idle - 1
            else:
                # Another team can arrive during dispatch. Confirm a formerly idle
                # slot became busy rather than assuming the total idle count fell.
                rows = self.queue_reader.read(frame) if world and self.queue_reader else None
                newly_busy = [r for r in rows or [] if r.number in idle_rows
                              and r.state in ("mining", "outbound", "busy") and r.seconds is not None]
                correct = world and len(newly_busy) == 1
            stable = stable + 1 if correct else 0
            if stable >= 2:
                return
            if time.monotonic() >= deadline:
                now=time.monotonic()
                if now-getattr(self,'_departure_diagnostic_at',float('-inf'))>=60:
                    self.emit("diagnostic", frame=frame, name="departure_confirmation", match=None, threshold=0)
                    self._departure_diagnostic_at=now
                raise DepartureUncertain(f"出发后的队列变化未确认（之前{old_idle}，当前{count}）；进入复查，不重复点击出发",old_idle,idle_rows)
            self.sleep(self.poll)

    def dispatch_one(self, continuous: bool = False) -> None:
        started = time.monotonic()
        for attempt in range(1, 4):
            idle_rows = None
            if continuous:
                # Refresh candidates after every search, as other teams can arrive.
                rows = self.stable_queues()
                idle_rows = {r.number for r in rows if r.state in ("idle", "returning")}
                if not any(r.state == "idle" for r in rows):
                    raise NoIdleQueues("打开搜索前已经没有空闲队伍；未点击搜索")
            idle = self.open_world_search()
            self.press("search_button")
            self.sleep(float(self.config.get("search_wait_seconds", 2)))
            try:
                self.press("gather")
                break
            except TemplateTimeout as exc:
                # Only a pre-departure search failure is recoverable. A popup,
                # open search/march panel or missing queue must still stop input.
                if exc.name != "gather" or not self.search_retry_allowed(exc.frame):
                    raise
                self.log(f"搜索后未出现采集按钮；已确认世界地图和空闲队伍（第{attempt}/3次）")
                if attempt == 3:
                    raise SearchUnavailable("连续3次搜索未出现采集按钮；未点击出发") from exc
                self.log(f"3秒后重新搜索{self.config.get('target_level',7)}级红水晶")
                self.emit("search_retry", delay=3)
                self.sleep(3)
        # Once gathering was clicked, no search retry surrounds departure.
        self.press("depart")
        self.confirm_departure(idle, idle_rows)
        self.completed += 1
        self.log(f"已确认补队；累计派出 {self.completed} 队；本次耗时 {time.monotonic() - started:.2f} 秒")

    def search_retry_allowed(self, failed_frame: np.ndarray) -> bool:
        for frame in (failed_frame, self.session.capture()):
            if (not self.matches("search_icon", frame) or self.matches("depart", frame)
                    or self.matches("search_button", frame)):
                return False
        if self.queue_reader is not None:
            rows = self.stable_queues()
            return all(r.state != "unknown" for r in rows) and any(r.state == "idle" for r in rows)
        return self.stable_idle_count() > 0

    def run(self) -> None:
        self.require_ready()
        total = int(self.config.get("dispatches", 3))
        for number in range(1, total + 1):
            self.check_stop()
            self.log(f"准备第 {number}/{total} 队")
            self.emit("progress", completed=self.completed, total=total)
            for attempt in range(4):
                try:
                    self.dispatch_one()
                    break
                except TemplateTimeout as exc:
                    if attempt==3:raise
                    self.log(f'{exc}；恢复页面后重新检查队伍（{attempt+1}/3）')
                    self.ensure_world(reset=True)
            self.emit("progress", completed=self.completed, total=total)
            self.emit("queue", idle=self.stable_idle_count(), capacity=self.capacity)
            self.log(f"第 {number}/{total} 队已通过队列变化确认出发")
        self.log(f"一个大循环完成：确认派出 {total} 队；本次停止。")

    def stable_queues(self) -> list[QueueRow]:
        if self.queue_reader is None:
            self.queue_reader = QueueReader(self.base, self.templates["idle"], self.detector, self.capacity)
        deadline = time.monotonic() + self.timeout
        previous = None
        stable = 0
        generation = getattr(self.session, "generation", 0)
        while True:
            self.check_stop()
            captured_at=time.monotonic()
            frame = self.session.capture()
            rows = self.queue_reader.read(frame)
            current_generation = getattr(self.session, "generation", 0)
            if generation != current_generation:
                generation, previous, stable = current_generation, None, 0
                deadline = time.monotonic() + self.timeout
            coherent = rows and previous and len(rows)==len(previous)==self.capacity and all(
                a.state == b.state and ((a.seconds is None and b.seconds is None) or
                (a.seconds is not None and b.seconds is not None and 0 <= b.seconds - a.seconds <= 3))
                for a, b in zip(rows, previous))
            stable = stable + 1 if coherent else (1 if rows else 0)
            if stable >= 3:
                self.queue_observed_at=captured_at
                return rows
            previous = rows
            if time.monotonic() >= deadline:
                now = time.monotonic()
                last = getattr(self, "_queue_diagnostic_at", None)
                if last is None or now - last >= 60:
                    self.emit("diagnostic", frame=frame, name="queue_timers", match=None, threshold=0)
                    self._queue_diagnostic_at = now
                raise QueueUnavailable("无法稳定识别左侧队伍栏；本次不点击，等待重新读取")
            self.sleep(self.poll)

    def run_continuous(self) -> None:
        self.require_ready()
        if not self.matches('search_icon'):self.ensure_world()
        watcher=None
        if self.config.get('chat_invites',False) and self.capacity==4:
            from chat_invites import ChatWatcher,ChatPageChanged
            watcher=ChatWatcher(self)
            from carriage_quota import CarriageQuota
            quota=CarriageQuota(self)
            try:
                watcher.close_if_open()
                remaining=quota.check()
            except (TemplateTimeout,ChatPageChanged) as exc:
                self.log(f'马车页面未就绪：{exc}；恢复采矿，本次关闭马车监测')
                self.ensure_world(reset=True)
                remaining=None
            if remaining is None or remaining<=0:
                self.log('马车次数不足或未确认：关闭本次聊天挂机，继续世界红水晶采集')
                watcher=None
        target = int(self.config.get("dispatches", 3))
        self.log(f"持续采集开启：目标在外 {target} 队；计时归零后等待返程，空闲后补队")
        if not self.matches("search_icon"):
            self.ensure_world()
        self.wait("search_icon")
        pending=None
        recoveries=0
        queue_failures=0
        queue_resets=0
        while True:
            self.check_stop()
            # A hidden/changed screen must not be treated as an idle queue.
            in_chat=bool(watcher and watcher.images.visible(self.session.capture()))
            if not in_chat:
                if not self.matches('search_icon'):self.ensure_world()
            try:
                rows = self.stable_queues()
                queue_failures=0
                queue_resets=0
            except QueueUnavailable as exc:
                if in_chat:
                    watcher.close_if_open()
                if self.expand_collapsed_queues():continue
                queue_failures+=1
                if queue_failures>=3:
                    if queue_resets>=3:raise QueueUnavailable('队伍栏已恢复3次仍无法确认，请查看诊断截图') from exc
                    self.log('连续三次无法读取队伍栏，尝试回城并重新进入世界')
                    if self.ensure_world(reset=True) is not True:
                        from visual_navigation import PageUnavailable
                        raise PageUnavailable('未确认完成回城和重新进入世界，不计为队伍栏恢复')
                    queue_resets+=1
                    self.log(f'已确认回城并重新进入世界；第{queue_resets}/3次队伍栏重置完成')
                    queue_failures=0
                self.wait_for_queue_retry(exc)
                continue
            if in_chat and not watcher.all_mining(rows,self.capacity):
                watcher.close_if_open()
                continue  # Refresh all queue states after closing chat.
            idle = sum(r.state == "idle" for r in rows)
            busy = self.capacity - idle
            self.emit("queue", idle=idle, capacity=self.capacity)
            self.emit("occupancy", busy=busy, target=target, completed=self.completed)
            if pending:
                try:
                    self.confirm_departure(pending.old_idle,pending.idle_rows)
                except DepartureUncertain:
                    self.log('尚未确认上次出发结果：保持监测，不重复派队；5秒后复查')
                    self.sleep(5)
                    continue
                self.completed+=1;pending=None;recoveries=0
                self.log(f'恢复后已确认上次派队成功；累计派出 {self.completed} 队')
                continue
            if idle and busy < target:
                self.log("；".join(r.display() for r in rows))
                self.log(f"确认存在空闲队伍，立即补充{self.config.get('target_level',7)}级红水晶采集")
                self.emit("monitor", rows=rows, delay=0, completed=self.completed)
                try:
                    self.dispatch_one(continuous=True)
                    recoveries=0
                except DepartureUncertain as exc:
                    pending=exc
                    self.log(f'{exc}；先恢复世界地图，再检查队伍变化')
                    self.ensure_world(reset=True)
                except TemplateTimeout as exc:
                    recoveries+=1
                    if recoveries>3:raise WorkflowError('同一派队流程连续恢复3次仍失败，请检查诊断截图') from exc
                    self.log(f'{exc}；第{recoveries}/3次恢复页面后重试，重新读取队伍')
                    self.ensure_world(reset=True)
                except NoIdleQueues:
                    # This exception is raised before opening search/departure.
                    # A team may have changed state since the prior observation.
                    self.log("搜索前复核已无空闲队伍；未派队，2秒后重新检查")
                    self.emit("queue_recheck", delay=2)
                    self.sleep(2)
                except SearchUnavailable as exc:
                    self.log(f"{exc}；等待30秒后重新检查队伍并搜索，F8可停止")
                    self.emit("search_retry", delay=30)
                    self.sleep(30)
                except QueueUnavailable as exc:
                    # This type is only raised by queue reads before departure.
                    # A failed post-departure confirmation remains fatal.
                    self.wait_for_queue_retry(exc)
                continue
            delay = next_check_seconds(rows, self.config.get("monitor_max_seconds", 30),
                                       self.config.get("monitor_fallback_seconds", 2))
            self.log("；".join(r.display() for r in rows) + f"；{delay:g}秒后检查")
            self.emit("monitor", rows=rows, delay=delay, completed=self.completed)
            # Event.wait is interruptible: F8 cancels even a distant alarm immediately.
            if watcher and watcher.all_mining(rows,self.capacity):
                # Refresh after each attempted join, even if its result was
                # ambiguous. The page's counter is authoritative.
                if watcher.quota_dirty:
                    try:
                        remaining=quota.check()
                    except TemplateTimeout as exc:
                        self.log(f'马车复查失败：{exc}；本次只采矿')
                        remaining=None
                    self.ensure_world()
                    if remaining is None or remaining<=0:
                        self.log('马车次数不足或未确认：关闭本次聊天挂机，继续世界红水晶采集')
                        watcher=None
                        continue
                    watcher.quota_dirty=False
                    rows=self.stable_queues()
                    if not watcher.all_mining(rows,self.capacity):continue
                try:
                    watcher.wait(rows,observed_at=getattr(self,'queue_observed_at',None))
                except (TemplateTimeout,ChatPageChanged) as exc:
                    self.log(f'聊天页面发生变化：{exc}；退出并恢复世界采矿，本次不再监测马车')
                    watcher=None
                    self.ensure_world(reset=True)
            else:
                self.sleep(delay)

    def wait_for_queue_retry(self, error: QueueUnavailable) -> None:
        delay = 10
        self.log(f"{error}；{delay}秒后复查，F8可停止")
        self.emit("queue_wait", delay=delay)
        self.sleep(delay)

    def preview(self) -> None:
        frame = self.session.capture()
        self.log(f"客户区 {frame.shape[1]}x{frame.shape[0]}；当前可见匹配：")
        visible = {}
        for name in self.templates:
            if name == "level_six":
                continue
            matches = self.matches(name, frame)
            visible[name] = matches
            if matches:
                self.log(f"{name}: {[(round(m.score, 3), m.center) for m in matches]}")
        if self.detector:
            for item in self.detector.detect(frame, [0, 0.12, 0.2, 0.45]):
                if item.name not in self.templates:
                    visible.setdefault(item.name, []).append(item.match)
        plus = self.matches("plus", frame)
        if plus:
            self.log(f"当前识别等级：{self.read_level(frame, plus[0])}；目标{self.config.get('target_level',7)}级")
        if visible.get("search_icon"):
            self.emit("queue", idle=len(visible.get("idle", [])), capacity=self.capacity)
            self.queue_reader = QueueReader(self.base, self.templates["idle"], self.detector, self.capacity)
            rows = self.queue_reader.read(frame)
            if rows:
                self.log("；".join(r.display() for r in rows))
                self.emit("monitor", rows=rows, delay=None, completed=self.completed)
        self.emit("preview", frame=frame, matches=visible)
        self.log("预览结束，没有点击。")


def gather(config_path: Path, execute: bool, start_delay: float) -> None:
    config = read_gather_config(config_path)
    if start_delay > 0:
        print(f"{start_delay:g} 秒后开始，请把游戏切到前台。", flush=True)
        time.sleep(start_delay)
    session = GameSession(config["window_title"], target_size=tuple(config["window_size"]), logger=print)
    bot = CrystalGatherer(config, config_path.parent, session)
    if execute:
        bot.run_continuous() if config.get("continuous", True) else bot.run()
    else:
        bot.preview()
