# 本地 YOLO 模型

`yolo26s.pt` 是 COCO 预训练起点，不能直接操作游戏。
训练导出的 `topheroes.onnx` / `topheroes.json` 可在助手中选择。
训练脚本默认标记 `stage: unverified`；使用 `--bootstrap` 时标记 `bootstrap`。这两种状态都只允许识别预览。
独立游戏截图和实际坐标验证完成后才能部署为 `verified`。
仓库包含 `topheroes`（控件）、`topheroes-queue`（队伍）和 `topheroes-chat`（聊天按钮）三套 ONNX 与对应 JSON 清单。清单中的 `sha256` 必须与模型一致；运行版使用同一组文件。

训练权重、训练截图、运行日志和个人配置不随仓库提供。模型验证范围以各清单及测试记录为准，不能将聊天按钮的静态验证当作实际加入商队成功。
