# Steam Top Heroes 日常助手

通过游戏窗口截图识别状态、使用鼠标操作的 Windows 采集助手。识别用 YOLO ONNX 本地推理与 OpenCV 模板匹配，不读取或修改游戏内存、不注入游戏进程。

最新版本：**v2026.10.25**（断线自动恢复、Steam 重复启动处理、超时救援、前台接管、连续补队提速）。

## 在另一台电脑上使用

### 方式一：下载免安装包（推荐，不需要装 Python）

1. 打开 [Releases](../../releases/latest)，下载 `TopHeroesHelper-YOLO-v2026.10.25-win64.zip`。
2. 解压到任意目录（例如 `D:\TopHeroesHelper`），保持内部结构不变：

   ```text
   TopHeroesHelper-YOLO.exe
   README-Windows.txt
   SHA256SUMS.txt
   models/topheroes.onnx
   models/topheroes.json
   models/topheroes-queue.onnx
   models/topheroes-queue.json
   models/topheroes-chat.onnx
   models/topheroes-chat.json
   ```

3. 双击 `TopHeroesHelper-YOLO.exe`。首次运行在「连接与识别」页选中 `models/topheroes.onnx`。
4. 校验文件完整性（可选）：

   ```powershell
   Get-FileHash .\TopHeroesHelper-YOLO.exe -Algorithm SHA256
   # 与 SHA256SUMS.txt 中的值比对，应一致
   ```

   SHA256SUMS.txt 也可以单独从 Release 页面下载。

系统要求：Windows 10/11 x64、CPU 推理、无需 Python 和 CUDA。游戏客户区必须能容纳 1600×900（另加标题栏边框）。EXE 未做代码签名，SmartScreen 提示时选「更多信息 → 仍要运行」。

### 方式二：从源码运行

```powershell
py -3.11 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python topheroes_gui.py
```

模型文件不在仓库里（`models/*.onnx`、`models/*.json` 被 `.gitignore` 忽略），需要按方式一从 Release 下载后放入 `models/`。重新构建单文件 EXE：

```powershell
.venv\Scripts\python -m pip install -r requirements-build.txt
.\build_exe.ps1
```

### 换电脑时要注意

- **模型必须单独下载**，仓库里只有 `models/README.md`；缺模型时界面无法识别，只能看预览。
- **游戏要通过 Steam 启动**。启动按钮和超时救援使用 `steam://rungameid/4007740` 拉起游戏，这样才能沿用你自己配置的游戏加速/代理；直接双击游戏 exe 会绕过代理。
- **设置和日志不随包携带**。新电脑从默认值开始：矿点 7 级、队列总数 4、目标 4、马车监测关闭。设置与日志在 `%LOCALAPPDATA%\TopHeroesHelper`，识别失败截图在同目录的 `diagnostics`。
- **屏幕分辨率**要能放下 1600×900 客户区，笔记本 125%/150% 缩放下尤其要先确认。
- EXE 已内置 `msvcp140.dll` / `vcruntime140*.dll`；若系统仍报缺 DLL，装一次 [VC++ 2015-2022 运行库](https://aka.ms/vs/17/release/vc_redist.x64.exe)。

## 核心功能

- YOLO ONNX 本地 CPU 检测与 OpenCV 模板识别，支持识别预览（只显示匹配，不点击）。
- 支持 3/4 队、1～10 级红水晶选择，提供单轮采集和持续补队。
- 识别空闲、行军、采集、返程状态及倒计时，按队伍变化确认出发；数字用游戏字体模板识别，不依赖在线 OCR。
- **断线自动恢复**：只认已确认的「连接服务器失败」弹窗与蓝色确认按钮，两帧确认后点击，重试间隔至少 30 秒；恢复后重新读取全部队伍，不重放被打断的「出发」。
- **Steam 重复启动处理**：识别「游戏已在运行」提示，用 UI 自动化按下按钮后重试，不使用屏幕坐标或全局按键。
- **超时救援**：连续 300 秒未确认采集进展时，才按 Steam 拉起游戏并重建窗口；只看进展，不看鼠标是否空闲。
- **前台接管 30 秒**：游戏切到后台后等待 30 秒再判定离开，短暂切窗口不中断任务。
- **连续补队提速**：批量处理 YOLO 候选框、缩短固定等待，实机回放中位耗时 4.73 s → 3.21 s。
- 可选公会马车监测，根据采矿倒计时退出监测并恢复补队；每次启动默认关闭。
- 处理弹窗、队伍栏折叠与遮挡后的页面恢复；回城重置须确认城内及重新进入世界地图。
- 标准游戏客户区 1600×900，检查窗口位置、尺寸和前台状态，支持 F8 立即停止。
- Windows EXE 与配套模型见 [Releases](../../releases/latest)。

界面默认 1160×800，最小 1000×700：左侧「任务配置」「连接与识别」，右侧运行状态与四队独立倒计时，「识别画面」与「运行日志」可切换，底部启动/停止始终保留。

## 审查与验证

- 状态识别只使用截图；窗口接口仅用于窗口识别、位置、尺寸、前台检查，以及救援时的启动游戏。
- 识别坐标须还原到游戏客户区；窗口变化后废弃旧坐标，重新识别。
- 缺失或遮挡的队伍不当作空闲；出发结果未确认时不重复派队。
- 回城按钮连续确认后才点击；进入城内、再返回世界地图均经连续画面确认，才增加恢复次数。
- 断线、救援、前台接管、Steam 重启都只在执行模式下动作；识别预览与截图采集不会自动点击。
- 2026-10-25 本机验证记录：195 项通过、1 项跳过，7 项因历史原始截图未随仓库提供而未计入；冻结版本的重启/界面/F8/断线回放通过；真实模型回放中位耗时 4.7343 s → 3.2143 s。
- 待实机验证：本次修复后完整返程补队整段循环、整夜稳定性、马车成功加入的实际结果。

详细记录见 [队伍遮挡恢复审查](tests/queue_occlusion_review.md)、[断线自动恢复](tests/connection_recovery_review.md)、[YOLO 部署验证](tests/yolo_deployment_test.md)、[流程提速](tests/speed_test.md)。
