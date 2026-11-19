param([Parameter(Mandatory=$true)][string]$CecilPath)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compiler)) { throw '.NET Framework 4 C# compiler is required.' }
Copy-Item -LiteralPath $CecilPath -Destination (Join-Path $PSScriptRoot 'Mono.Cecil.dll') -Force
function Compile([string[]]$CompilerArgs) {
    & $compiler /nologo @CompilerArgs
    if ($LASTEXITCODE -ne 0) { throw 'C# compilation failed.' }
}
Compile @('/target:library','/out:TcpProxyHook.dll','TcpProxyHook.cs')
Compile @('/out:VerifyTcpHook.exe','/r:TcpProxyHook.dll','VerifyTcpHook.cs')
Compile @('/target:winexe','/out:TopHeroesProxy.exe','/r:System.Web.Extensions.dll','/r:System.Windows.Forms.dll','SteamProxyLauncher.cs')
foreach ($name in @('ProxyPatch','PatchRequestProxy','VerifyRequestProxy','InspectNetworkSettings')) {
    Compile @("/out:$name.exe",'/r:Mono.Cecil.dll',"$name.cs")
}
Compile @('/out:VerifyProxy.exe','VerifyProxy.cs')
Write-Host 'Built tools only. No game files or Steam settings were changed.'
