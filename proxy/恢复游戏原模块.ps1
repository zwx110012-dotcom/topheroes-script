$ErrorActionPreference = 'Stop'
if (Get-Process -Name 'TopHeroes_Steam','TopHeroes' -ErrorAction SilentlyContinue) { throw 'Close Top Heroes first.' }
$gameRoot = $env:TOPHEROES_GAME_ROOT
if (-not $gameRoot) { throw 'Set TOPHEROES_GAME_ROOT first.' }
$assemblyRoot = Join-Path $gameRoot 'TopHeroes_Steam_Data\StreamingAssets\Assemblies'
$specs = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'manifest.json') -Raw | ConvertFrom-Json
. (Join-Path $PSScriptRoot '模块路径.ps1')
function Get-Sha256([string]$path) {
    $hasher = [System.Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($hasher.ComputeHash([IO.File]::ReadAllBytes($path))).Replace('-', '') } finally { $hasher.Dispose() }
}
foreach ($spec in $specs) {
    if ($spec.name -notin @('ScriptProj','ScriptThirdParty')) { throw 'Unexpected module name.' }
    $spec.target = Get-GameModulePath $spec
    $backup = Join-Path $PSScriptRoot ($spec.name + '.original.mdl')
    $target = $spec.target
    if ((Get-Sha256 $backup) -ne $spec.originalHash) { throw ('Backup mismatch: ' + $spec.name) }
    $hash = Get-Sha256 $target
    if ($hash -ne $spec.patchHash -and $hash -ne $spec.originalHash) { throw ('Game module changed; refusing to overwrite: ' + $spec.name) }
}
foreach ($spec in $specs) {
    $target = $spec.target
    if ((Get-Sha256 $target) -eq $spec.patchHash) { Copy-Item -LiteralPath (Join-Path $PSScriptRoot ($spec.name + '.original.mdl')) -Destination $target -Force }
    if ((Get-Sha256 $target) -ne $spec.originalHash) { throw ('Restore failed: ' + $spec.name) }
}
Write-Host 'Original game modules verified. Network and VPN settings were not changed.'
