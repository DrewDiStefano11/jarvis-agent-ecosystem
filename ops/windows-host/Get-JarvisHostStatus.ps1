[CmdletBinding()]
param([string]$ConfigPath = (Join-Path $PSScriptRoot 'jarvis-host.json'), [switch]$AsJson)
Import-Module (Join-Path $PSScriptRoot 'JarvisHost.Common.psm1') -Force
$cfg = Import-JarvisHostConfig -Path $ConfigPath
$metaB = Read-JarvisProcessMetadata -Path (Join-Path $cfg.stateDirectory 'backend.json')
$metaF = Read-JarvisProcessMetadata -Path (Join-Path $cfg.stateDirectory 'frontend.json')
$backendOwn = if ($metaB.pid) { Test-JarvisProcessOwnership -PID $metaB.pid -ExpectedExecutable $metaB.executable -ExpectedWorkingDir $metaB.workingDirectory -ExpectedArguments $metaB.arguments } else { $false }
$frontendOwn = if ($metaF.pid) { Test-JarvisProcessOwnership -PID $metaF.pid -ExpectedExecutable $metaF.executable -ExpectedWorkingDir $metaF.workingDirectory -ExpectedArguments $metaF.arguments } else { $false }
$status = @{ configValid = $true; backendHealth = (Test-JarvisHealthEndpoint -Url $cfg.backendHealthUrl -TimeoutSec $cfg.healthTimeoutSec); frontendHealth = (Test-JarvisHealthEndpoint -Url $cfg.frontendHealthUrl -TimeoutSec $cfg.healthTimeoutSec); backendOwned = $backendOwn; frontendOwned = $frontendOwn; crashLoop = (Get-JarvisCrashLoopLatch -LatchPath (Join-Path (Resolve-JarvisHostPath -Path $cfg.stateDirectory -BaseDir $env:LOCALAPPDATA) 'crash_loop.json')) }
if ($AsJson) { $status | ConvertTo-Json -Depth 5 } else { $status | Format-List | Out-String | Write-Output }
