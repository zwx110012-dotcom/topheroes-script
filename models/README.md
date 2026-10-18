# 本地 YOLO 模型

`yolo26s.pt` 是 COCO 预训练起点，不能直接操作游戏。
训练导出的 `topheroes.onnx` / `topheroes.json` 可在助手中选择。
训练脚本默认标记 `stage: unverified`；使用 `--bootstrap` 时标记 `bootstrap`。这两种状态都只允许识别预览。
独立游戏截图和实际坐标验证完成后才能部署为 `verified`。
推理模型及训练截图留在本机，Git 默认忽略。
