$ErrorActionPreference='Stop'
$stage=$PSScriptRoot
$cases=@('clean','leftovers','unknown_second','bad_backup','validate_only')
$results=@()
function Get-Sha256([string]$path){return (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash}
function Write-TrialLog([string]$message){$script:messages.Add($message)}
foreach($case in $cases){
    $root=Join-Path $stage ('隔离测试\'+$case)
    New-Item -ItemType Directory -Path $root -Force | Out-Null
    foreach($name in @('代理模块预检.ps1','模块路径.ps1')){Copy-Item -LiteralPath (Join-Path $stage ('..\'+$name)) -Destination (Join-Path $root $name)}
    . (Join-Path $root '模块路径.ps1')
    . (Join-Path $root '代理模块预检.ps1')
    $script:messages=New-Object System.Collections.Generic.List[string]
    $specs=@(foreach($name in @('ScriptThirdParty','ScriptProj')){
        $original=Join-Path $root ($name+'.original.mdl')
        $patched=Join-Path $root ($name+'.proxy.mdl')
        $target=Join-Path $root ($name+'.current.mdl')
        [IO.File]::WriteAllText($original,('original-'+$name))
        [IO.File]::WriteAllText($patched,('proxy-'+$name))
        Copy-Item -LiteralPath $(if($case -eq 'clean'){$original}else{$patched}) -Destination $target -Force
        [pscustomobject]@{name=$name;target=$target;originalHash=Get-Sha256 $original;patchHash=Get-Sha256 $patched}
    })
    if($case -eq 'unknown_second'){[IO.File]::WriteAllText($specs[1].target,'unknown-new-game-version')}
    if($case -eq 'bad_backup'){[IO.File]::WriteAllText((Join-Path $root 'ScriptProj.original.mdl'),'corrupt-backup')}
    $before=@($specs | ForEach-Object {Get-Sha256 $_.target})
    $blocked=$false
    try{Initialize-GameModules $specs -ValidateOnly:($case -eq 'validate_only')}
    catch{
        if($case -notin @('unknown_second','bad_backup')){throw}
        if($_.Exception.Message -notmatch 'neither original|Backup hash mismatch'){throw}
        $blocked=$true
    }
    if($case -in @('unknown_second','bad_backup')){
        if(!$blocked){throw ('Did not block '+$case)}
        for($i=0;$i -lt 2;$i++){if((Get-Sha256 $specs[$i].target) -ne $before[$i]){throw 'Partial restore before validation failure'}}
    }elseif($case -eq 'validate_only'){
        for($i=0;$i -lt 2;$i++){if((Get-Sha256 $specs[$i].target) -ne $before[$i]){throw 'ValidateOnly changed a module'}}
    }else{
        foreach($spec in $specs){if((Get-Sha256 $spec.target) -ne $spec.originalHash){throw ('Restoration failed '+$case)}}
    }
    $results+=@{case=$case;ok=$true;blocked=$blocked}
}
$results | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $stage '隔离恢复测试.json') -Encoding utf8
$results | ConvertTo-Json -Depth 4
