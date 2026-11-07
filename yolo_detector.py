"""ONNX pixel detector. All returned boxes are in original client screenshot pixels."""
from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import native_runtime
import cv2
import numpy as np

from top_heroes import Match, WorkflowError

CLASS_NAMES = ["world", "search_icon", "resources", "red_crystal", "minus", "plus",
               "level_six", "search_button", "gather", "depart", "idle", "queue_header",
               "queue_mining", "queue_returning", "queue_fast_forward", "timer_region",
               "merchant_invite", "merchant_join"]

# Screen-edge controls need a stable local scale and diverse local backgrounds.
# These crops are trained explicitly; they are not per-button guessed coordinates.
NAVIGATION_ROIS = {"world": [0.7, 0.65, 1, 1], "search_icon": [0, 0.5, 0.2, 0.9]}
QUEUE_ROI = [0, 0.12, 0.2, 0.45]
QUEUE_IDLE_CONFIDENCE = 0.60


def linked_queue_model(path: Path) -> Path | None:
    manifest = path.with_suffix('.json')
    if not manifest.is_file():
        return None
    name = json.loads(manifest.read_text(encoding='utf-8')).get('queue_model')
    if name is None:
        return None
    if name != 'topheroes-queue.onnx' or path.name == name:
        raise ValueError('队伍模型必须是同目录的topheroes-queue.onnx，不能循环引用')
    return path.with_name(name)


def require_verified_model(path: Path, allow_queue: bool = False, capacity: int | None = None,
                           _loaded_digests: dict[Path, str] | None = None):
    path = Path(path)
    try:
        info = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if _loaded_digests is not None and _loaded_digests.get(path) != digest:
            raise ValueError("当前模型文件已更新，请重新开始以加载新模型")
        if info.get("stage") != "verified" or info.get("sha256") != digest:
            raise ValueError("模型尚未通过独立游戏截图和点击坐标验证")
        if info.get("navigation_roi_version") != 1:
            raise ValueError("模型未验证导航区域识别，请使用配套新版模型")
        if info.get('role') == 'queue' and not allow_queue:
            raise ValueError('这是队伍专用模型，请选择topheroes.onnx作为主模型')
        if allow_queue and info.get('role') != 'queue':
            raise ValueError('配套队伍模型的用途未验证')
        linked = linked_queue_model(path)
        if capacity is not None and not linked and capacity not in info.get('supported_queue_counts',[3]):
            raise ValueError(f'队伍模型尚未验证{capacity}队，请使用配套训练模型')
        if linked:
            require_verified_model(linked, allow_queue=True, capacity=capacity,
                                   _loaded_digests=_loaded_digests)
    except (OSError, ValueError, KeyError) as exc:
        raise WorkflowError(f"YOLO 当前仅可预览：{exc}。请先补充截图并验证模型") from exc


@dataclass(frozen=True)
class Detection:
    name: str
    match: Match


@dataclass(frozen=True)
class PixelTransform:
    origin_x: int
    origin_y: int
    width: int
    height: int
    resized_width: int
    resized_height: int
    pad_x: int
    pad_y: int

    def restore(self, xyxy) -> tuple[int, int, int, int]:
        # Use actual rounded resize dimensions, not an assumed uniform scale.
        x0, y0, x1, y1 = xyxy
        xs = np.clip((np.array([x0, x1]) - self.pad_x) * self.width / self.resized_width,
                     0, self.width)
        ys = np.clip((np.array([y0, y1]) - self.pad_y) * self.height / self.resized_height,
                     0, self.height)
        return (self.origin_x + int(np.floor(xs[0])), self.origin_y + int(np.floor(ys[0])),
                self.origin_x + int(np.ceil(xs[1])), self.origin_y + int(np.ceil(ys[1])))


def prepare_input(frame: np.ndarray, size: tuple[int, int], roi=None):
    height, width = frame.shape[:2]
    region = roi or [0, 0, 1, 1]
    if len(region) != 4 or not all(np.isfinite(v) for v in region):
        raise WorkflowError("YOLO 搜索区域无效")
    x0, y0, x1, y1 = [round(v * d) for v, d in zip(region, [width, height, width, height])]
    if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
        raise WorkflowError("YOLO 搜索区域超出截图")
    crop = frame[y0:y1, x0:x1]
    target_w, target_h = size
    scale = min(target_w / crop.shape[1], target_h / crop.shape[0])
    rw, rh = max(1, round(crop.shape[1] * scale)), max(1, round(crop.shape[0] * scale))
    pad_x, pad_y = (target_w - rw) // 2, (target_h - rh) // 2
    canvas = np.full((target_h, target_w, 3), 114, np.uint8)
    canvas[pad_y:pad_y + rh, pad_x:pad_x + rw] = cv2.resize(crop, (rw, rh))
    tensor = np.ascontiguousarray(canvas[:, :, ::-1].transpose(2, 0, 1)[None], dtype=np.float32) / 255
    transform = PixelTransform(x0, y0, crop.shape[1], crop.shape[0], rw, rh, pad_x, pad_y)
    return tensor, transform


def decode_output(output: np.ndarray, names: dict[int, str], transform: PixelTransform,
                  confidence: float, iou: float = 0.45) -> list[Detection]:
    data = np.asarray(output)
    if data.ndim != 3 or data.shape[0] != 1:
        raise WorkflowError(f"不支持的 YOLO 输出形状：{data.shape}")
    candidates = []
    if data.shape[-1] == 6:  # YOLO26 native end-to-end: xyxy, confidence, class id.
        for x0, y0, x1, y1, score, class_id in data[0]:
            if np.isfinite([x0, y0, x1, y1, score, class_id]).all() and score >= confidence:
                idx = int(round(float(class_id)))
                if abs(class_id - idx) < 1e-4 and idx in names:
                    candidates.append((idx, float(score), (x0, y0, x1, y1)))
    elif data.shape[1] == 4 + len(names):  # Ultralytics one-to-many xywh + class scores.
        # The 960px head has 18,900 proposals. Filter those in NumPy before
        # restoring boxes; a Python loop over every rejected proposal dominated
        # CPU postprocessing even when only one button was visible.
        rows = data[0].T
        finite = np.isfinite(rows).all(axis=1)
        valid_rows = rows[finite]
        classes = np.argmax(valid_rows[:, 4:], axis=1)
        scores = valid_rows[np.arange(len(valid_rows)), 4 + classes]
        kept = np.flatnonzero(scores >= confidence)
        for k in kept:
            idx, score = int(classes[k]), float(scores[k])
            cx, cy, w, h = valid_rows[k, :4]
            candidates.append((idx, score, (cx-w/2, cy-h/2, cx+w/2, cy+h/2)))
    else:
        raise WorkflowError(f"不支持的 YOLO 检测头：{data.shape}")
    results = []
    for idx in names:
        group = [(score, transform.restore(box)) for cls, score, box in candidates if cls == idx]
        boxes = [[x0, y0, x1-x0, y1-y0] for _, (x0, y0, x1, y1) in group]
        if not boxes:
            continue
        keep = cv2.dnn.NMSBoxes(boxes, [score for score, _ in group], confidence, iou)
        for k in np.asarray(keep).reshape(-1):
            score, (x0, y0, x1, y1) = group[int(k)]
            if x1 > x0 and y1 > y0:
                results.append(Detection(names[idx], Match(score, x0, y0, x1-x0, y1-y0)))
    return sorted(results, key=lambda d: d.match.score, reverse=True)


class YoloDetector:
    def __init__(self, path: Path, confidence: float = 0.65, queue_component: bool = False):
        path = Path(path)
        if not path.is_file():
            raise WorkflowError(f"YOLO 模型尚未准备好：{path}；先采集截图并训练，或选择已有 ONNX 模型")
        if not 0 < confidence < 1:
            raise WorkflowError("YOLO 置信度必须在0和1之间")
        self.loaded_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        try:
            import onnxruntime as ort
            options = ort.SessionOptions()
            options.intra_op_num_threads = 4
            # Button and queue sessions share a four-core CPU. Sleeping idle
            # workers leaves those cores available to the active model/game.
            options.add_session_config_entry("session.intra_op.allow_spinning", "0")
            self.session = ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])
            inputs = self.session.get_inputs()
            if len(inputs) != 1 or inputs[0].type != "tensor(float)":
                raise ValueError("只支持单个 float32 图像输入")
            self.input_name = inputs[0].name
            shape = inputs[0].shape
            if len(shape) != 4 or shape[:2] != [1, 3] or not all(isinstance(v, int) for v in shape[2:]):
                raise ValueError("模型需要固定的 [1,3,H,W] 输入")
            self.size = (shape[3], shape[2])
            metadata = self.session.get_modelmeta().custom_metadata_map
            names = ast.literal_eval(metadata["names"])
            self.names = {int(k): str(v) for k, v in (enumerate(names) if isinstance(names, list) else names.items())}
            if (set(self.names.values()) not in (set(CLASS_NAMES),set(CLASS_NAMES[:16]))
                    or set(self.names)!=set(range(len(self.names)))):
                raise ValueError("模型类别不是本项目的游戏控件；COCO通用模型不能操作游戏")
        except ImportError as exc:
            raise WorkflowError("缺少 ONNX Runtime，请安装 requirements.txt") from exc
        except Exception as exc:
            raise WorkflowError(f"无法加载 YOLO 模型：{exc}") from exc
        self.path, self.confidence = path, confidence
        self.queue_component = queue_component
        if hashlib.sha256(path.read_bytes()).hexdigest() != self.loaded_sha256:
            raise WorkflowError("模型在加载期间被替换，请重新开始")
        self._frame, self._cache = None, {}
        try:
            linked = linked_queue_model(path)
            self.queue_detector = YoloDetector(linked, confidence, queue_component=True) if linked else None
        except (OSError, ValueError) as exc:
            raise WorkflowError(f'无法加载配套队伍模型：{exc}') from exc

    def require_verified(self):
        # One full hash per file per check verifies BOTH the loaded session and
        # its manifest. Do not cache hashes across calls: even a same-size edit
        # with restored timestamps must block the next mouse action.
        queue = getattr(self,'queue_detector',None)
        if linked_queue_model(self.path) != (queue.path if queue else None):
            raise WorkflowError('队伍模型配置已更新，请重新开始')
        loaded_digests = {self.path: self.loaded_sha256}
        if queue:
            loaded_digests[queue.path] = queue.loaded_sha256
        require_verified_model(self.path, allow_queue=getattr(self,'queue_component',False),
                               _loaded_digests=loaded_digests)

    def require_capacity(self, capacity: int):
        queue = getattr(self,'queue_detector',None)
        path = queue.path if queue else self.path
        info = json.loads(path.with_suffix('.json').read_text(encoding='utf-8'))
        if capacity not in info.get('supported_queue_counts',[3]):
            raise WorkflowError(f'队伍模型尚未验证{capacity}队，请使用配套训练模型；当前只能预览')

    def detect(self, frame: np.ndarray, roi=None) -> list[Detection]:
        queue = getattr(self,'queue_detector',None)
        if queue and roi is not None and tuple(roi) == tuple(QUEUE_ROI):
            return queue.detect(frame, roi)
        if frame is not self._frame:
            self._frame, self._cache = frame, {}
        key = tuple(roi or [0, 0, 1, 1])
        if key not in self._cache:
            tensor, transform = prepare_input(frame, self.size, key)
            output = self.session.run(None, {self.input_name: tensor})[0]
            idle_threshold = min(self.confidence, QUEUE_IDLE_CONFIDENCE)
            threshold = idle_threshold if key == tuple(QUEUE_ROI) else self.confidence
            candidates = decode_output(output, self.names, transform, threshold)
            self._cache[key] = [d for d in candidates if d.match.score >=
                (idle_threshold if key == tuple(QUEUE_ROI) and d.name == 'idle' else self.confidence)]
        return self._cache[key]

    def matches(self, name: str, frame: np.ndarray, roi=None) -> list[Match]:
        # Panels share full-screen inference; navigation and queues have fixed,
        # explicitly trained crops. restore() adds each crop's client origin.
        crop = QUEUE_ROI if name == "idle" else NAVIGATION_ROIS.get(name)
        detections = self.detect(frame, crop)
        height, width = frame.shape[:2]
        region = roi or [0, 0, 1, 1]
        x0,y0,x1,y1 = [v*d for v,d in zip(region,[width,height,width,height])]
        return [d.match for d in detections if d.name == name and
                x0 <= d.match.center[0] <= x1 and y0 <= d.match.center[1] <= y1]
