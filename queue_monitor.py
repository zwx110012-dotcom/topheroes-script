"""Read queue icons and HH:MM:SS from visible pixels; schedule the next check."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from top_heroes import WorkflowError, load_template


STATE_NAMES = {"idle": "空闲", "mining": "采集中", "outbound": "前往采集点", "returning": "返程中",
               "busy": "行军/其他忙碌", "unknown": "未识别"}


def white_mask(image: np.ndarray) -> np.ndarray:
    low, high = image.min(axis=2), image.max(axis=2)
    return ((low > 175) & ((high.astype(int) - low) < 75)).astype(np.uint8) * 255


def text_glyphs(image: np.ndarray) -> list[np.ndarray]:
    mask = white_mask(image)
    columns = np.any(mask, axis=0)
    edges = np.diff(np.r_[False, columns, False].astype(int))
    glyphs = []
    for left, right in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)):
        part = mask[:, left:right]
        ys = np.flatnonzero(np.any(part, axis=1))
        # The world can shine through the translucent queue bar. Ignore tiny
        # background specks; real digits and the two-dot colon span >=5 pixels.
        if len(ys) and ys[-1] - ys[0] + 1 >= 5:
            glyphs.append(part[ys[0]:ys[-1] + 1])
    return glyphs


def normalize_glyph(glyph: np.ndarray) -> np.ndarray:
    # Keep aspect ratio: a narrow 1 must not be stretched into another digit.
    h, w = glyph.shape
    # The game font is fixed at 1600x900. Preserve its pixel size: rescaling
    # 9- and 10-pixel antialiased glyphs to identical heights distorts strokes.
    scale = min(2, 16 / w, 24 / h)
    nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
    resized = cv2.resize(glyph, (nw, nh), interpolation=cv2.INTER_AREA) / 255.0
    canvas = np.zeros((28, 20), np.float32)
    x, y = (20 - nw) // 2, (28 - nh) // 2
    canvas[y:y + nh, x:x + nw] = resized
    return cv2.GaussianBlur(canvas, (3, 3), 0.6)


class TimerReader:
    def __init__(self, path: Path):
        with np.load(path, allow_pickle=False) as bank:
            self.samples = bank["samples"].astype(np.float32)
            self.labels = bank["labels"].astype(int)
        if set(self.labels) != set(range(10)):
            raise WorkflowError("倒计时数字模板不完整")

    def read(self, image: np.ndarray) -> tuple[int, str, float] | None:
        glyphs = text_glyphs(image)
        if len(glyphs) != 8:
            return None
        digits, confidence = [], 1.0
        for index, glyph in enumerate(glyphs):
            h, w = glyph.shape
            if index in (2, 5):
                # Colons must contain two separate dots, rather than a digit 1.
                count, _, stats, _ = cv2.connectedComponentsWithStats(glyph)
                if w > 3 or h < 5 or count != 3 or any(s[4] > 8 for s in stats[1:]):
                    return None
                continue
            if not (4 <= w <= 10 and 8 <= h <= 15):
                return None
            normalized = normalize_glyph(glyph)
            scores = 1 - np.mean((self.samples - normalized) ** 2, axis=(1, 2))
            by_digit = np.array([scores[self.labels == d].max() for d in range(10)])
            order = np.argsort(by_digit)
            best, runner_up = float(by_digit[order[-1]]), float(by_digit[order[-2]])
            if best < 0.93 or best - runner_up < 0.012:
                return None
            digits.append(str(order[-1]))
            confidence = min(confidence, best)
        hh, mm, ss = (int("".join(digits[i:i + 2])) for i in (0, 2, 4))
        if mm > 59 or ss > 59:
            return None
        return hh * 3600 + mm * 60 + ss, f"{hh:02}:{mm:02}:{ss:02}", confidence


@dataclass(frozen=True)
class QueueRow:
    number: int
    state: str
    seconds: int | None = None
    text: str | None = None

    def display(self) -> str:
        return f"{self.number}队 {STATE_NAMES[self.state]}" + (f" {self.text}" if self.text else "")


def next_check_seconds(rows: list[QueueRow], maximum: float = 30,
                       fallback: float = 2) -> float:
    busy = [r for r in rows if r.state != "idle"]
    if not busy or any(r.seconds is None for r in busy):
        return fallback
    # Wake shortly before zero, then re-read the icon and the new return timer.
    return max(0.5, min(maximum, min(r.seconds for r in busy) - 1))


class QueueReader:
    def __init__(self, base: Path, idle_template: np.ndarray):
        self.header = load_template(base / "templates/queue_header.png")
        self.icons = {name: [load_template(p) for p in sorted(
            (base / "templates").glob(f"queue_{name}*.png"))]
                      for name in ("mining", "returning")}
        self.idle_template = idle_template
        self.fast_forward = load_template(base / "templates/queue_fast_forward.png")
        self.timer = TimerReader(base / "templates/timer_digits.npz")

    def read(self, frame: np.ndarray) -> list[QueueRow] | None:
        # Imported here to keep the reusable matcher and gatherer independent.
        from crystal_gather import find_matches
        headers = find_matches(frame, self.header, [1], 0.9, [0, 0.12, 0.14, 0.24], 0.5)
        if len(headers) != 1:
            return None
        header = headers[0]
        rows = []
        for index in range(3):
            x, y = header.x + 34, header.y + 30 + 36 * index
            label = frame[y:y + 25, x:x + 70]
            if label.shape[:2] != (25, 70):
                return None
            idle = find_matches(label, self.idle_template, [0.95, 1, 1.05], 0.86, blur_sigma=1)
            if idle:
                rows.append(QueueRow(index + 1, "idle"))
                continue
            # Only the little status badge is sampled; the hero portrait is excluded.
            icon_crop = frame[y + 7:y + 31, header.x + 3:header.x + 29]
            matches = {}
            for name, templates in self.icons.items():
                for template in templates:
                    h, w = template.shape[:2]
                    yy, xx = np.ogrid[:h, :w]
                    # Exclude even the portrait pixels behind the badge's corners.
                    mask = (((xx - (w - 1) / 2) ** 2 + (yy - (h - 1) / 2) ** 2)
                            <= 6.3 ** 2).astype(np.uint8)
                    response = cv2.matchTemplate(white_mask(icon_crop), white_mask(template),
                                                cv2.TM_CCORR_NORMED, mask=mask)
                    finite = response[np.isfinite(response)]
                    score = float(finite.max()) if finite.size else 0
                    if score >= 0.88:
                        matches[name] = max(matches.get(name, 0), score)
            state = max(matches, key=matches.get) if matches else "unknown"
            if state == "mining":
                button = frame[y:y + 31, header.x + 104:header.x + 139]
                if find_matches(button, self.fast_forward, [0.95, 1, 1.05], 0.9):
                    state = "outbound"
            timer = self.timer.read(label)
            if timer:
                state = "busy" if state == "unknown" else state
                seconds, text, _ = timer
                rows.append(QueueRow(index + 1, state, seconds, text))
            else:
                rows.append(QueueRow(index + 1, state))
        if all(r.state == "unknown" for r in rows):
            return None  # Folded/occluded list is never interpreted as three free queues.
        return rows
