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
    if not 1 <= int(config.get("dispatches", 3)) <= 3:
        raise WorkflowError("每个大循环的 dispatches 只能是 1 到 3")
    size = config.get("window_size", [1600, 900])
    if not isinstance(size, list) or len(size) != 2 or any(type(v) is not int or v < 400 for v in size):
        raise WorkflowError("window_size 必须是两个至少400像素的整数")
    config["window_size"] = size
    if type(config.get("continuous", True)) is not bool:
        raise WorkflowError("continuous 必须为 true 或 false")
    for key, default, lower, upper in [("monitor_max_seconds", 30, 1, 60),
                                       ("monitor_fallback_seconds", 2, 0.5, 10)]:
        value = float(config.get(key, default))
        if not lower <= value <= upper:
            raise WorkflowError(f"{key} 必须在 {lower} 到 {upper} 秒之间")
        config[key] = value
    return config


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
                frame = screenshot(self.hwnd)
            except WindowChanged:
                continue
            if (frame.shape[1], frame.shape[0]) == self.size:
                break
        else:
            raise WorkflowError("截图期间窗口持续改变，已停止点击")
        if self.on_frame:
            self.on_frame(frame)
        return frame

    def click(self, point: tuple[int, int]) -> None:
        if self.ensure_size():
            raise WindowChanged("已恢复窗口，需重新识别目标后点击")
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
        self.queue_reader: QueueReader | None = None
        self.base = base

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
        found = find_matches(frame, self.templates[name], self.scales,
                            float(spec.get("confidence", self.config.get("confidence", 0.9))),
                            roi if roi is not None else spec.get("roi"),
                            float(spec.get("blur_sigma", 0)))
        self.check_stop()
        return found

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
                best = find_matches(frame, self.templates[name], self.scales, 0,
                                    spec.get("roi"), float(spec.get("blur_sigma", 0)))
                match = best[0] if best else None
                threshold = float(spec.get("confidence", self.config.get("confidence", 0.9)))
                self.emit("diagnostic", frame=frame, name=name, match=match, threshold=threshold)
                score = f"最高得分 {match.score:.3f}，要求 {threshold:.3f}" if match else "搜索区域无可用匹配"
                raise TemplateTimeout(name, frame, f"等待 {name} 图像超时（{score}）；未点击")
            self.sleep(self.poll)

    def press(self, name: str) -> Match:
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
        if count > 3:
            raise WorkflowError("空闲队列图像匹配超过3处；模板存在歧义")
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
        return bool(self.matches("level_six", self.level_region(frame, plus), [0, 0, 1, 1]))

    def set_level_six(self) -> None:
        plus = self.wait("plus")
        frame = self.session.capture()
        if self.is_six(frame, plus):
            self.log("已从图像确认等级为6")
            return
        unchanged = 0
        limit = int(self.config.get("max_level_adjustments", 16))
        for _ in range(limit):
            before = self.level_region(frame, plus)
            self.press("minus")
            frame = self.session.capture()
            if self.is_six(frame, plus):
                self.log("已从图像确认等级为6")
                return
            after = self.level_region(frame, plus)
            diff = float(np.abs(before.astype(float) - after.astype(float)).mean())
            unchanged = unchanged + 1 if diff < 1.5 else 0
            if unchanged >= 2:
                break
        else:
            raise WorkflowError("减号调整达到上限，仍无法确认等级下限")
        for _ in range(limit):
            self.press("plus")
            frame = self.session.capture()
            if self.is_six(frame, plus):
                self.log("已从图像确认等级为6")
                return
        raise WorkflowError("加号调整达到上限，仍未识别到6级；停止搜索")

    def open_world_search(self) -> int:
        if not self.matches("search_icon"):
            self.press("world")
        self.wait("search_icon")
        count = self.stable_idle_count()
        self.log(f"搜索前图像确认：空闲 {count}/3 队")
        self.emit("queue", idle=count)
        if count == 0:
            raise NoIdleQueues("未识别到空闲队列；队列已满或列表被折叠，停止派队")
        self.press("search_icon")
        self.press("resources")
        self.press("red_crystal")
        self.set_level_six()
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
                self.log(f"出发后图像识别：空闲 {count}/3 队，预期 {old_idle - 1}/3")
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
                self.emit("diagnostic", frame=frame, name="departure_confirmation", match=None, threshold=0)
                raise WorkflowError(f"出发后的队列变化未确认（之前{old_idle}，当前{count}）；不重复点击出发")
            self.sleep(self.poll)

    def dispatch_one(self, continuous: bool = False) -> None:
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
                self.log("3秒后重新搜索6级红水晶")
                self.emit("search_retry", delay=3)
                self.sleep(3)
        # Once gathering was clicked, no search retry surrounds departure.
        self.press("depart")
        self.confirm_departure(idle, idle_rows)
        self.completed += 1
        self.log(f"已确认补队；累计派出 {self.completed} 队")

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
        total = int(self.config.get("dispatches", 3))
        for number in range(1, total + 1):
            self.check_stop()
            self.log(f"准备第 {number}/{total} 队")
            self.emit("progress", completed=self.completed, total=total)
            self.dispatch_one()
            self.emit("progress", completed=self.completed, total=total)
            self.emit("queue", idle=self.stable_idle_count())
            self.log(f"第 {number}/{total} 队已通过队列变化确认出发")
        self.log(f"一个大循环完成：确认派出 {total} 队；本次停止。")

    def stable_queues(self) -> list[QueueRow]:
        if self.queue_reader is None:
            self.queue_reader = QueueReader(self.base, self.templates["idle"])
        deadline = time.monotonic() + self.timeout
        previous = None
        stable = 0
        generation = getattr(self.session, "generation", 0)
        while True:
            self.check_stop()
            frame = self.session.capture()
            rows = self.queue_reader.read(frame)
            current_generation = getattr(self.session, "generation", 0)
            if generation != current_generation:
                generation, previous, stable = current_generation, None, 0
                deadline = time.monotonic() + self.timeout
            coherent = rows and previous and all(
                a.state == b.state and ((a.seconds is None and b.seconds is None) or
                (a.seconds is not None and b.seconds is not None and 0 <= b.seconds - a.seconds <= 3))
                for a, b in zip(rows, previous))
            stable = stable + 1 if coherent else (1 if rows else 0)
            if stable >= 3:
                return rows
            previous = rows
            if time.monotonic() >= deadline:
                self.emit("diagnostic", frame=frame, name="queue_timers", match=None, threshold=0)
                raise WorkflowError("无法稳定识别左侧队伍栏；请展开列表并关闭遮挡，已停止点击")
            self.sleep(self.poll)

    def run_continuous(self) -> None:
        target = int(self.config.get("dispatches", 3))
        self.log(f"持续采集开启：目标在外 {target} 队；计时归零后等待返程，空闲后补队")
        if not self.matches("search_icon"):
            self.press("world")
        self.wait("search_icon")
        while True:
            self.check_stop()
            # A hidden/changed screen must not be treated as an idle queue.
            self.wait("search_icon")
            rows = self.stable_queues()
            idle = sum(r.state == "idle" for r in rows)
            busy = 3 - idle
            self.emit("queue", idle=idle)
            self.emit("occupancy", busy=busy, target=target, completed=self.completed)
            if idle and busy < target:
                self.log("；".join(r.display() for r in rows))
                self.log("确认存在空闲队伍，立即补充6级红水晶采集")
                self.emit("monitor", rows=rows, delay=0, completed=self.completed)
                try:
                    self.dispatch_one(continuous=True)
                except SearchUnavailable as exc:
                    self.log(f"{exc}；等待30秒后重新检查队伍并搜索，F8可停止")
                    self.emit("search_retry", delay=30)
                    self.sleep(30)
                continue
            delay = next_check_seconds(rows, self.config.get("monitor_max_seconds", 30),
                                       self.config.get("monitor_fallback_seconds", 2))
            self.log("；".join(r.display() for r in rows) + f"；{delay:g}秒后检查")
            self.emit("monitor", rows=rows, delay=delay, completed=self.completed)
            # Event.wait is interruptible: F8 cancels even a distant alarm immediately.
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
        plus = self.matches("plus", frame)
        if plus:
            self.log(f"等级6识别结果：{self.is_six(frame, plus[0])}")
        if visible.get("search_icon"):
            self.emit("queue", idle=len(visible.get("idle", [])))
            self.queue_reader = QueueReader(self.base, self.templates["idle"])
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
