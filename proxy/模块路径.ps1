function Get-GameModulePath($spec) {
    if ($spec.name -notin @('ScriptProj', 'ScriptThirdParty')) { throw 'Unexpected module name.' }
    $install = Join-Path $assemblyRoot ($spec.name + '.mdl')
    $cache = Join-Path (Join-Path $env:USERPROFILE 'AppData\LocalLow\Rivergame\Steam Top Heroes\RemoteAssets\Assemblies') ($spec.name + '.mdl')
    if ($spec.target -ne $install -and $spec.target -ne $cache) { throw 'Unexpected module location.' }
    foreach ($candidate in @($cache, $install)) {
        $dll = [IO.Path]::ChangeExtension($candidate, '.dll')
        if ([IO.File]::Exists($dll)) { throw ('Game module format changed; needs a new proxy package: ' + $spec.name) }
        if ([IO.File]::Exists($candidate)) { return $candidate }
    }
    throw ('No game module available: ' + $spec.name + '. Check Steam installed files.')
}

function Restore-InstalledModule($spec) {
    $target = [string]$spec.target
    if (-not [IO.File]::Exists($target)) {
        Write-TrialLog ('Game removed module; leaving it absent: ' + $spec.name)
        return
    }
    $hash = Get-Sha256 $target
    if ($hash -eq $spec.originalHash) {
        Write-TrialLog ('Original module already present: ' + $spec.name)
        return
    }
    if ($hash -ne $spec.patchHash) {
        Write-TrialLog ('Game updated module; keeping the new file: ' + $spec.name)
        return
    }
    $backup = Join-Path $PSScriptRoot ($spec.name + '.original.mdl')
    if ((Get-Sha256 $backup) -ne $spec.originalHash) { throw ('Backup mismatch: ' + $spec.name) }
    Copy-Item -LiteralPath $backup -Destination $target -Force
    if ((Get-Sha256 $target) -ne $spec.originalHash) { throw ('Restore verification failed: ' + $spec.name) }
    Write-TrialLog ('Original module restored and hash verified: ' + $spec.name)
}
