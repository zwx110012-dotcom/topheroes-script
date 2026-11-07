"""Reduce duplicate disk work without accepting replaced loaded models."""
import hashlib
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from top_heroes import WorkflowError
from yolo_detector import YoloDetector


def paired_detector(folder):
    main = folder / 'topheroes.onnx'
    queue = folder / 'topheroes-queue.onnx'
    main.write_bytes(b'buttons')
    queue.write_bytes(b'queues!')
    parts = []
    for path, role in ((main, 'buttons'), (queue, 'queue')):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        info = dict(stage='verified', navigation_roi_version=1, role=role,
                    sha256=digest, supported_queue_counts=[3, 4])
        if role == 'buttons':
            info['queue_model'] = queue.name
        path.with_suffix('.json').write_text(json.dumps(info), encoding='utf-8')
        detector = YoloDetector.__new__(YoloDetector)
        detector.path, detector.loaded_sha256 = path, digest
        detector.queue_component = role == 'queue'
        detector.queue_detector = None
        parts.append(detector)
    parts[0].queue_detector = parts[1]
    return parts[0]


def test_each_action_rehashes_both_models_exactly_once(tmp_path):
    detector = paired_detector(tmp_path)
    reads = []
    original = Path.read_bytes

    def counted(path):
        reads.append(path)
        return original(path)

    with patch.object(Path, 'read_bytes', counted):
        for _ in range(2):
            detector.require_verified()
            detector.require_capacity(4)
    assert reads == [detector.path, detector.queue_detector.path] * 2


@pytest.mark.parametrize('component', ['buttons', 'queue'])
def test_same_size_replacement_with_restored_timestamp_is_blocked(tmp_path, component):
    detector = paired_detector(tmp_path)
    detector.require_verified()
    target = detector if component == 'buttons' else detector.queue_detector
    before = target.path.stat()
    target.path.write_bytes(b'changed')
    os.utime(target.path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert target.path.stat().st_size == before.st_size
    with pytest.raises(WorkflowError, match='模型文件已更新'):
        detector.require_verified()


def test_updated_manifest_cannot_authorize_a_replaced_loaded_queue(tmp_path):
    detector = paired_detector(tmp_path)
    detector.require_verified()
    target = detector.queue_detector.path
    target.write_bytes(b'changed')
    manifest = target.with_suffix('.json')
    info = json.loads(manifest.read_text())
    info['sha256'] = hashlib.sha256(target.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(info))
    with pytest.raises(WorkflowError, match='模型文件已更新'):
        detector.require_verified()


def test_manifest_and_queue_link_are_still_checked_on_every_action(tmp_path):
    detector = paired_detector(tmp_path)
    detector.require_verified()
    manifest = detector.path.with_suffix('.json')
    info = json.loads(manifest.read_text())
    info['stage'] = 'preview'
    manifest.write_text(json.dumps(info))
    with pytest.raises(WorkflowError):
        detector.require_verified()
    info['stage'] = 'verified'
    info.pop('queue_model')
    manifest.write_text(json.dumps(info))
    with pytest.raises(WorkflowError, match='队伍模型配置已更新'):
        detector.require_verified()
