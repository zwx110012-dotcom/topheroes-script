function Initialize-GameModules($specs, [switch]$ValidateOnly) {
    # Validate the complete pair before changing either file. Unknown updates
    # must stay untouched even if the other module is a recognized stale patch.
    $recover = New-Object System.Collections.Generic.List[object]
    foreach ($spec in $specs) {
        $target = [string]$spec.target
        $original = Join-Path $PSScriptRoot ($spec.name + '.original.mdl')
        $patched = Join-Path $PSScriptRoot ($spec.name + '.proxy.mdl')
        if ((Get-Sha256 $original) -ne $spec.originalHash) { throw ('Backup hash mismatch: ' + $spec.name) }
        if ((Get-Sha256 $patched) -ne $spec.patchHash) { throw ('Trial hash mismatch: ' + $spec.name) }
        $current = Get-Sha256 $target
        if ($current -eq $spec.originalHash) { continue }
        if ($current -ne $spec.patchHash) { throw ('Game module is neither original nor a known proxy; package needs updating: ' + $spec.name) }
        $recover.Add($spec)
    }
    if ($ValidateOnly) {
        Write-TrialLog ('Preflight passed; recognized leftover modules=' + $recover.Count + '; no files changed.')
        return
    }
    foreach ($spec in $recover) {
        # Recheck immediately before restoring; do not replace a concurrent
        # game update between the validation pass and this write.
        $current = Get-Sha256 $spec.target
        if ($current -eq $spec.originalHash) { continue }
        if ($current -ne $spec.patchHash) { throw ('Game module changed during recovery: ' + $spec.name) }
        Restore-InstalledModule $spec
        if ((Get-Sha256 $spec.target) -ne $spec.originalHash) { throw ('Recovery verification failed: ' + $spec.name) }
        Write-TrialLog ('Recovered known proxy left by a previous interrupted run: ' + $spec.name)
    }
}
