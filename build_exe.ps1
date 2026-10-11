$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
& '.\.venv\Scripts\python.exe' -m PyInstaller --noconfirm --clean --onefile --windowed --name TopHeroesHelper --add-data 'gather_crystal.yaml;.' --add-data 'templates;templates' --exclude-module pytest --exclude-module matplotlib topheroes_gui.py
if ($LASTEXITCODE -ne 0) { throw 'EXE build failed' }
