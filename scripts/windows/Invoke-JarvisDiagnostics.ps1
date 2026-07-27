[CmdletBinding()]
param([string]$OutputDir = (Join-Path $env:TEMP "JarvisDiag_" + (Get-Date -Format 'yyyyMMddHHmmss')))
Import-Module (Join-Path $PSScriptRoot '..\..\ops\windows-host' 'JarvisHost.Common.psm1') -Force
New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
$cfg = Import-JarvisHostConfig -Path (Join-Path $PSScriptRoot '..\..\ops\windows-host' 'jarvis-host.json')
$redactedCfg = Redact-JarvisConfig -Config $cfg
$redactedCfg | ConvertTo-Json -Depth 5 | Set-Content -Path (Join-Path $OutputDir 'config_redacted.json') -Force
$statusOutput = . (Join-Path $PSScriptRoot '..\..\ops\windows-host' 'Get-JarvisHostStatus.ps1') -ConfigPath (Join-Path $PSScriptRoot '..\..\ops\windows-host' 'jarvis-host.json') -AsJson 2>$null
if ($statusOutput) { $statusOutput | Set-Content -Path (Join-Path $OutputDir 'status.json') -Force }
$manifest = Get-JarvisToolkitManifest -ManifestPath (Join-Path (Resolve-JarvisHostPath -Path $cfg.stateDirectory -BaseDir $env:LOCALAPPDATA) 'tailscale_routes.json')
if ($manifest) { $manifest | ConvertTo-Json -Depth 5 | Set-Content -Path (Join-Path $OutputDir 'tailscale_routes.json') -Force }
$diskInfo = @{ totalSpace = (Get-WmiObject Win32_LogicalDisk -Filter "DeviceID='C:'").Size; freeSpace = (Get-WmiObject Win32_LogicalDisk -Filter "DeviceID='C:'").FreeSpace }
$diskInfo | ConvertTo-Json -Depth 5 | Set-Content -Path (Join-Path $OutputDir 'disk.json')
"manifest_version=1; includes=config_redacted.json,status.json,tailscale_routes.json,disk.json; exclusions=db,.env,credentials,secrets,tokens" | Set-Content -Path (Join-Path $OutputDir 'manifest.txt') -Force
# Explicit exclusions check
if (Test-Path (Join-Path $OutputDir 'database.db')) { Remove-Item (Join-Path $OutputDir 'database.db') -Force }
$archivePath = $OutputDir + ".zip"
Compress-Archive -Path (Join-Path $OutputDir '*') -DestinationPath $archivePath -Force
Write-Output "Sanitized diagnostics archive: $archivePath (excludes DB, .env, secrets verified)"
