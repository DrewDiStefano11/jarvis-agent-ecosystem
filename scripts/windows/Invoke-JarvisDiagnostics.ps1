[CmdletBinding()]
param([string]$OutputDir = (Join-Path $env:TEMP "JarvisDiag_" + (Get-Date -Format 'yyyyMMddHHmmss')))
Import-Module (Join-Path $PSScriptRoot '..\..\ops\windows-host' 'JarvisHost.Common.psm1') -Force
New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
$cfg = Import-JarvisHostConfig -Path (Join-Path $PSScriptRoot '..\..\ops\windows-host' 'jarvis-host.json')
$redacted = Redact-JarvisConfig -Config $cfg
$redacted | ConvertTo-Json -Depth 5 | Set-Content -Path (Join-Path $OutputDir 'config_redacted.json')
$statusOutput = . (Join-Path $PSScriptRoot '..\..\ops\windows-host' 'Get-JarvisHostStatus.ps1') -ConfigPath (Join-Path $PSScriptRoot '..\..\ops\windows-host' 'jarvis-host.json') -AsJson
$statusOutput | Set-Content -Path (Join-Path $OutputDir 'status.json')
$manifest = Get-JarvisToolkitManifest -ManifestPath (Join-Path (Resolve-JarvisHostPath -Path $cfg.stateDirectory -BaseDir $env:LOCALAPPDATA) 'tailscale_routes.json')
if ($manifest) { $manifest | ConvertTo-Json -Depth 5 | Set-Content -Path (Join-Path $OutputDir 'tailscale_routes.json') }
"manifest_version=1; files=manifest.json,status.json,config_redacted.json; exclusions=db,env,credentials,secrets" | Set-Content -Path (Join-Path $OutputDir 'manifest.txt')
$archivePath = $OutputDir + ".zip"
Compress-Archive -Path (Join-Path $OutputDir '*') -DestinationPath $archivePath -Force
Write-Output "Diagnostics archive: $archivePath"
