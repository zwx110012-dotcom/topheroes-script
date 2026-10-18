$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv-yolo\Scripts\python.exe')) {
    & '.\.venv\Scripts\python.exe' -m venv .venv-yolo
    if ($LASTEXITCODE -ne 0) { throw 'Training environment creation failed' }
}
foreach ($environment in @('.venv', '.venv-yolo')) {
    # Private venv startup only; do not change the user's Anaconda installation.
    $startupFile = Join-Path $PSScriptRoot ($environment + '\Lib\site-packages\sitecustomize.py')
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'native_runtime.py') -Destination $startupFile -Force
}
& '.\.venv-yolo\Scripts\python.exe' -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw 'pip installation failed' }
& '.\.venv-yolo\Scripts\python.exe' -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
if ($LASTEXITCODE -ne 0) { throw 'CUDA PyTorch installation failed' }
& '.\.venv-yolo\Scripts\python.exe' -m pip install -r requirements-yolo.txt
if ($LASTEXITCODE -ne 0) { throw 'YOLO training dependencies installation failed' }
& '.\.venv\Scripts\python.exe' -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Runtime dependencies installation failed' }
