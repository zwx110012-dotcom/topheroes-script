# YOLO 游戏识别

## 当前结构

- 模型：YOLO26s，16类目标，使用人工复核的真实游戏截图微调。当前ONNX固定输入960×960。已部署版本分别使用按钮模型和队伍区域模型，避免局部补训练影响其他控件。
- 训练：独立 `.venv-yolo`，CUDA PyTorch，RTX 3070 Ti 8 GB。
- 推理：ONNX Runtime CPU，固定 float32 输入，不把 PyTorch 打进 EXE。
- 导出采用与默认训练验证一致的常规检测头，并在本地按类别去重；另支持原生端到端输出。
- 界面可选择 yolo/template、ONNX 模型；可打开本地截图预览或保存游戏客户区截图。
- 绿框为模型位置，红十字为实际客户区预计点击点。模型缩放、补边和区域裁剪偏移统一还原。资源面板按钮共享全画面推理；世界入口、搜索入口和队伍栏使用各自固定区域放大识别，对应区域包含真实训练裁片和负例。
- 每帧缓存按截图对象和裁剪区域隔离；窗口移动或缩放会废弃旧点击坐标。
- 训练导出后模型默认只可预览。真实截图与坐标验证完成后，才可将对应哈希的部署状态设为 verified。

模型来源：[Ultralytics 官方仓库](https://github.com/ultralytics/ultralytics)。软件许可为 AGPL-3.0 或其商业许可；ONNX 导出不会自动免除许可要求。

## 截图与标注

界面「采集截图」在倒计时后切换到游戏，保存1600×900客户区画面，不派队。
位置：`%LOCALAPPDATA%\TopHeroesHelper\captures`。

需要覆盖：城内世界入口、世界地图与完整队伍栏、资源搜索各等级、采集面板、出发面板、返回世界后的队伍变化、弹窗/遮挡/队列收起等无目标情况。支持3或4队，第4行也必须标注。英雄头像需要变化；请提供完整原图，避免只发按钮裁片。

```powershell
.venv\Scripts\python.exe -X utf8 yolo_dataset.py import --sources captures tests/fixtures
.venv\Scripts\python.exe -X utf8 yolo_dataset.py import --sources "$env:LOCALAPPDATA\TopHeroesHelper\captures"
.venv\Scripts\python.exe -X utf8 yolo_dataset.py review
```

标注工具：选择类别后拖框，右键删除，复核所有可见目标后点「保存复核并下一张」。模板匹配生成的是标注草稿，不能当作人工真值。没有目标的真实画面可保存为空标注。截图集保存在本机 datasets 下，Git 忽略。

## 训练与导出

```powershell
.\setup_yolo.ps1
.venv-yolo\Scripts\python.exe -X utf8 train_yolo.py --epochs 80 --imgsz 960 --batch 4 --device 0
```

只使用已复核标注。正式训练自动从训练父图生成导航区域裁片，保持父图的train/val/test划分；验证图片不会生成训练裁片。`--bootstrap` 可以训练未复核草稿，目的仅为验证部署链路，输出不可用于自动点击。

输出 `models/topheroes.onnx`、`models/topheroes.json`。将二者复制到 EXE 旁的 models 文件夹，或在界面选择模型。模型配置的哈希须与 ONNX 相符；启用点击还需已验证的 `navigation_roi_version: 1`，旧版仅全画面训练的模型不能用于新版导航点击。

训练时 `review.json` 可明确标记 train、val、test。test 图片不进入训练和开发验证；从训练图裁出的队伍区域仍属于同一训练样本，不能当作独立验证。`validate_yolo.py` 同时检查未训练图上的检测框和真实队列/倒计时读取路径。

```powershell
.venv\Scripts\python.exe -X utf8 validate_yolo.py --model models/topheroes.onnx
```

倒计时数字模板通过实际字体截图校准。`calibrate_timer.py` 只接受人工确认的文本和区域，不把模型猜测自动写回数字模板。模板修改后需复查此前的数字识别样本。

按钮模型与队伍模型分别推理；倒计时仍由游戏字体数字识别读取。等级文字也在YOLO定位的加号旁按原始像素验证6级模板，不把全画面缩小后的检测类别当作数字确认。YOLO 检测框的变化可能导致数字裁剪失败，此时短间隔复查，不能把未知状态解释为空闲。

当前配套文件为topheroes.onnx/json与topheroes-queue.onnx/json。主模型清单的queue_model指向同目录队伍模型，两份哈希和部署状态都须有效；队伍专用模型不能作为按钮主模型启动。界面仍选择topheroes.onnx，自动加载配套队伍模型。

队列标题漏检时用原始像素核验固定文字，全部队伍锚点仍来自YOLO。队伍裁剪区域的空闲标签门槛单独校准为0.60，其他类别保持0.65；确认标题、与设置总数相符的完整队伍行和行列布局后，继续要求三帧稳定才补队。4队模式缺少第4行时等待复查，不视为空闲。模型清单须包含已验证的supported_queue_counts，未验证4队的模型只能预览4队画面。

## 验证要求

当前自动拆分是开发验证，不能证明跨场景稳定性。应另留不同会话的完整截图，覆盖不同窗口位置、尺寸、背景和英雄。按类检查漏检、误检、预计点击点是否位于真实按钮内部，验证点击后的状态转换。未完成这些步骤不能声称部署模型已可无人值守运行。
