$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$startupFile = Join-Path $PSScriptRoot '.venv\Lib\site-packages\sitecustomize.py'
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'native_runtime.py') -Destination $startupFile -Force
$cppRuntime = Join-Path $env:WINDIR 'System32'
& '.\.venv\Scripts\python.exe' -m PyInstaller --noconfirm --clean --onefile --windowed --name TopHeroesHelper-YOLO --add-data 'gather_crystal.yaml;.' --add-data 'templates;templates' --add-binary "$cppRuntime\msvcp140.dll;." --add-binary "$cppRuntime\vcruntime140.dll;." --add-binary "$cppRuntime\vcruntime140_1.dll;." --exclude-module pytest --exclude-module matplotlib topheroes_gui.py
if ($LASTEXITCODE -ne 0) { throw 'EXE build failed' }
$modelDestination = Join-Path $PSScriptRoot 'dist\models'
New-Item -ItemType Directory -Path $modelDestination -Force | Out-Null
foreach ($modelFilename in @('topheroes.onnx', 'topheroes.json', 'topheroes-queue.onnx', 'topheroes-queue.json', 'topheroes-chat.onnx', 'topheroes-chat.json')) {
    $modelSource = Join-Path $PSScriptRoot ('models\' + $modelFilename)
    if (Test-Path -LiteralPath $modelSource) {
        Copy-Item -LiteralPath $modelSource -Destination (Join-Path $modelDestination $modelFilename) -Force
    }
}
