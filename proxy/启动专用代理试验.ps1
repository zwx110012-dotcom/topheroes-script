param([switch]$ValidateOnly)
$ErrorActionPreference = 'Stop'
$gameRoot = $env:TOPHEROES_GAME_ROOT
if (-not $gameRoot) { throw 'Set TOPHEROES_GAME_ROOT to your game installation directory.' }
$assemblyRoot = Join-Path $gameRoot 'TopHeroes_Steam_Data\StreamingAssets\Assemblies'
$specs = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'manifest.json') -Raw | ConvertFrom-Json
$logFile = Join-Path $PSScriptRoot 'trial.log'
$readyFile = Join-Path $PSScriptRoot 'relay-ready.json'
$pythonExe = $env:TOPHEROES_PYTHON
if (-not $pythonExe) { $pythonExe = (Get-Command python -ErrorAction Stop).Source }
. (Join-Path $PSScriptRoot '模块路径.ps1')
. (Join-Path $PSScriptRoot '代理模块预检.ps1')
$steamCommand = @()
$steamCommandJson = [Environment]::GetEnvironmentVariable('TOPHEROES_STEAM_COMMAND')
if ($steamCommandJson) {
    $steamCommand = @(ConvertFrom-Json -InputObject $steamCommandJson)
    $expectedExe = Join-Path $gameRoot 'TopHeroes_Steam.exe'
    if ($steamCommand.Count -eq 0 -or [IO.Path]::GetFullPath([string]$steamCommand[0]) -ne $expectedExe) { throw 'Unexpected Steam game command.' }
}
function Get-Sha256([string]$path) {
    $hasher = [System.Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($hasher.ComputeHash([IO.File]::ReadAllBytes($path))).Replace('-', '') } finally { $hasher.Dispose() }
}
function Write-TrialLog([string]$message) {
    Add-Content -LiteralPath $logFile -Value ((Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + ' ' + $message)
}
$mutex = New-Object System.Threading.Mutex($false, 'Local\TopHeroesProxyTrial')
if (-not $mutex.WaitOne(0)) { throw 'Another trial launcher is active.' }
$installedModules = New-Object System.Collections.Generic.List[object]
$relayProcess = $null
try {
    if (Get-Process -Name 'TopHeroes_Steam','TopHeroes' -ErrorAction SilentlyContinue) { throw 'Close Top Heroes before starting this trial.' }
    foreach ($spec in $specs) {
        if ($spec.name -notin @('ScriptProj','ScriptThirdParty')) { throw 'Unexpected module name.' }
        $spec.target = Get-GameModulePath $spec
    }
    Initialize-GameModules $specs -ValidateOnly:$ValidateOnly
    if ($ValidateOnly) { return }
    $resolvedPort = & $pythonExe (Join-Path $PSScriptRoot 'resolve_proxy.py')
    if ($LASTEXITCODE -ne 0) { throw 'Current local VPN proxy is unavailable; check the VPN application.' }
    $proxyPort = [int]$resolvedPort
    if ($proxyPort -lt 1 -or $proxyPort -gt 65535) { throw 'Invalid resolved proxy port.' }
    Write-TrialLog ('Using current loopback proxy 127.0.0.1:' + $proxyPort)
    [IO.File]::Delete($readyFile)
    $relayStart = New-Object System.Diagnostics.ProcessStartInfo
    $relayStart.FileName = $pythonExe
    $relayStart.Arguments = '"' + (Join-Path $PSScriptRoot 'game_tcp_relay.py') + '"'
    $relayStart.WorkingDirectory = $PSScriptRoot
    $relayStart.UseShellExecute = $false
    $relayStart.CreateNoWindow = $true
    $relayStart.EnvironmentVariables['TOPHEROES_PROXY_PORT'] = [string]$proxyPort
    $relayProcess = [Diagnostics.Process]::Start($relayStart)
    $ready = $null
    for ($i=0; $i -lt 50; $i++) {
        if ($relayProcess.HasExited) { throw 'The local relay exited before readiness.' }
        if ([IO.File]::Exists($readyFile)) {
            try { $ready = Get-Content -LiteralPath $readyFile -Raw | ConvertFrom-Json } catch { $ready = $null }
            if ($ready -and $ready.pid -eq $relayProcess.Id) { break }
        }
        Start-Sleep -Milliseconds 200
    }
    if (-not $ready -or $ready.pid -ne $relayProcess.Id) { throw 'Local relay readiness timeout.' }
    foreach ($spec in $specs) {
        if ((Get-Sha256 $spec.target) -ne $spec.originalHash) { throw ('Game module changed during launch: ' + $spec.name) }
        Copy-Item -LiteralPath (Join-Path $PSScriptRoot ($spec.name + '.proxy.mdl')) -Destination $spec.target -Force
        $installedModules.Add($spec)
    }
    Write-TrialLog ('Installed trial modules; relay PID=' + $relayProcess.Id + '; routes and VPN settings unchanged.')
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = Join-Path $gameRoot 'TopHeroes_Steam.exe'
    $psi.WorkingDirectory = $gameRoot
    $psi.UseShellExecute = $false
    for ($i=1; $i -lt $steamCommand.Count; $i++) { $psi.ArgumentList.Add([string]$steamCommand[$i]) }
    [void]$psi.EnvironmentVariables.Remove('TOPHEROES_STEAM_COMMAND')
    $psi.EnvironmentVariables['TOPHEROES_PROXY'] = 'http://127.0.0.1:' + $proxyPort
    $psi.EnvironmentVariables['TOPHEROES_TCP_RELAY'] = [string]$ready.controlPort
    $psi.EnvironmentVariables['SteamAppId'] = '4007740'
    $psi.EnvironmentVariables['SteamGameId'] = '4007740'
    $gameProcess = [System.Diagnostics.Process]::Start($psi)
    Write-TrialLog ('Started game PID=' + $gameProcess.Id)
    $gameProcess.WaitForExit()
    Start-Sleep -Seconds 3
    while (Get-Process -Name 'TopHeroes_Steam','TopHeroes' -ErrorAction SilentlyContinue) { Start-Sleep -Seconds 2 }
    Write-TrialLog 'Game exited.'
} catch {
    Write-TrialLog ('ERROR: ' + $_.Exception.Message)
    throw
} finally {
    $gameStillRunning = Get-Process -Name 'TopHeroes_Steam','TopHeroes' -ErrorAction SilentlyContinue
    $restoreErrors = @()
    try {
        foreach ($spec in $installedModules) {
            if ($gameStillRunning) { Write-TrialLog ('Restore deferred; game still running: ' + $spec.name); continue }
            try { Restore-InstalledModule $spec }
            catch { $restoreErrors += $_.Exception.Message; Write-TrialLog ('RESTORE ERROR: ' + $_.Exception.Message) }
        }
    } finally {
        try {
            if ($relayProcess -and -not $gameStillRunning -and -not $relayProcess.HasExited) {
                $relayProcess.Kill()
                $relayProcess.WaitForExit()
                Write-TrialLog 'Stopped this trial local relay.'
            }
        } finally { $mutex.ReleaseMutex(); $mutex.Dispose() }
    }
    if ($restoreErrors.Count) { throw ('Module restore failed; check trial.log: ' + ($restoreErrors -join '; ')) }
}
