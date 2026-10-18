"""Bundle the current executable and its model siblings; verify after extraction."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import zipfile

ROOT=Path(__file__).resolve().parent
NAME='TopHeroes-Portable-Windows-x64'
STAGE=ROOT/'dist'/NAME
STAGE.mkdir(parents=True,exist_ok=True)
(STAGE/'models').mkdir(exist_ok=True)
files=['TopHeroesHelper-YOLO.exe']
for name in ('topheroes','topheroes-queue','topheroes-chat'):
    model=ROOT/'dist/models'/f'{name}.onnx'
    manifest=model.with_suffix('.json')
    info=json.loads(manifest.read_text(encoding='utf-8'))
    if info.get('stage')!='verified' or hashlib.sha256(model.read_bytes()).hexdigest()!=info['sha256']:
        raise ValueError(f'Model manifest failed: {name}')
    shutil.copy2(model,STAGE/'models'/model.name)
    info.pop('training_run',None)  # Development paths are not runtime dependencies.
    (STAGE/'models'/manifest.name).write_text(json.dumps(info,ensure_ascii=False,indent=2),encoding='utf-8')
    files.extend((f'models/{model.name}',f'models/{manifest.name}'))
shutil.copy2(ROOT/'dist/TopHeroesHelper-YOLO.exe',STAGE/'TopHeroesHelper-YOLO.exe')
(STAGE/'先读我.txt').write_text('''Top Heroes 日常助手 · Windows 64位便携版

使用方法
1. 把整个压缩包解压到本地文件夹，不要直接在压缩包内运行。
2. 保留 EXE 旁边的 models 文件夹及其中所有文件。
3. 启动 Steam Top Heroes Windows 客户端，再双击 TopHeroesHelper-YOLO.exe。
4. 选择队列总数、目标队数与矿点等级，点击“启动采集”。按F8停止。

运行要求
适用目标为 Windows 10/11 64位；其他电脑的系统与硬件尚未逐台实测。
不需要安装 Python、CUDA 或另装YOLO。模型使用CPU推理。
请保证屏幕能容纳1600×900游戏客户区及窗口边框，通常1920×1080或以上更合适。
桌面缩放后也必须有足够的实际可用区域。脚本运行时游戏需要保持前台且无遮挡。
不要把EXE单独复制走；模型丢失时无法使用YOLO识别。
如果游戏以管理员身份运行，助手需要同等权限才能点击。

功能
默认7级红水晶，已验证1～10级标签。队列总数支持3或4。
马车监测每次打开默认关闭，手动勾选后才读取贸易次数并监测聊天邀请。
支持从城内、世界及已知面板恢复；未知画面有恢复上限。
出发结果不明时持续复查，不重复派队。识别只读取截图，不读取游戏内存。

首次使用
不同电脑首次打开会使用该电脑的默认设置，不携带原电脑的账户、日志或个人配置。
如果这台电脑之前使用过旧版，模型路径可能仍指向旧目录；请通过“选择ONNX模型”选择
当前解压目录的 models/topheroes.onnx。
设置和日志保存在当前Windows用户的 %LOCALAPPDATA%\\TopHeroesHelper。

验证范围
当前EXE已在开发电脑完成实机采矿与页面恢复验证。
便携包另做异目录解压、干净配置启动和配套模型推理检查；不等同于已经实测另一台电脑。
商队点击加入后的真实结果仍未实机验证。
''',encoding='utf-8-sig')
files.append('先读我.txt')
hashes={name:hashlib.sha256((STAGE/name).read_bytes()).hexdigest() for name in files}
(STAGE/'文件校验.sha256').write_text('\n'.join(f'{digest}  {name}' for name,digest in hashes.items())+'\n',encoding='utf-8')
files.append('文件校验.sha256')
archive=ROOT/'dist/TopHeroes-Portable-Windows-x64-20261017.zip'
with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as bundle:
    for name in files:bundle.write(STAGE/name,f'{NAME}/{name}')
check=ROOT/'yolo-output/portable-verification-20261017'
check.mkdir(parents=True,exist_ok=True)
with zipfile.ZipFile(archive) as bundle:
    if bundle.testzip() is not None:raise ValueError('ZIP checksum failed')
    for name in files:
        target=check/NAME/name
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(bundle.read(f'{NAME}/{name}'))
for name,digest in hashes.items():
    if hashlib.sha256((check/NAME/name).read_bytes()).hexdigest()!=digest:
        raise ValueError(f'Extracted file differs: {name}')
env=os.environ.copy()
for key in list(env):
    if key.upper().startswith(('PYTHON','CONDA')) or key.upper()=='VIRTUAL_ENV':env.pop(key,None)
system_root=os.environ['SystemRoot']
env['PATH']=os.pathsep.join((str(Path(system_root)/'System32'),system_root))
env['LOCALAPPDATA']=str(check/'clean-user-config')
exe=check/NAME/'TopHeroesHelper-YOLO.exe'
def run_check(args,output):
    result=subprocess.run([str(exe),*map(str,args)],cwd=check/NAME,env=env,
                          creationflags=subprocess.CREATE_NO_WINDOW,timeout=40)
    report=json.loads(output.read_text(encoding='utf-8'))
    if result.returncode!=0 or not report.get('ok'):raise ValueError(report)
    return report
smoke=check/'startup.json'
run_check(['--smoke-test',smoke],smoke)
sample=check/'sample.jpg'
shutil.copy2(ROOT/'captures/recovery-20261017/01-world.jpg',sample)
for name in ('topheroes','topheroes-chat'):
    output=check/f'{name}-inference.json'
    run_check(['--yolo-smoke-test',check/NAME/'models'/f'{name}.onnx',sample,output],output)
summary={'ok':True,'archive':str(archive),'bytes':archive.stat().st_size,
         'archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),
         'exe_sha256':hashes['TopHeroesHelper-YOLO.exe'],
         'clean_config_startup':True,'python_environment_removed':True,
         'extracted_files_verified':len(hashes),'model_inference':True,'other_computer_tested':False}
(check/'result.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)
