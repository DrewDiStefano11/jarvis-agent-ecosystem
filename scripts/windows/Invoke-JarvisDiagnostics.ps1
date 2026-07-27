[CmdletBinding()]
param([string]$OutputDir = (Join-Path $env:TEMP "JarvisDiag_" + (Get-Date -Format 'yyyyMMddHHmmss')))
Import-Module (Join-Path $PSScriptRoot '..\..\ops\windows-host' 'JarvisHost.Common.psm1') -Force
New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
$cfg = Import-JarvisHostConfig -Path (Join-Path $PSScriptRoot '..\..\ops\windows-host' 'jarvis-host.json')
# Redacted config
$redactedCfg = Redact-JarvisConfig -Config $cfg
$redactedCfg | ConvertTo-Json -Depth 5 | Set-Content -Path (Join-Path $OutputDir 'config_redacted.json') -Force
# Status
try {
  $statusOutput = . (Join-Path $PSScriptRoot '..\..\ops\windows-host' 'Get-JarvisHostStatus.ps1') -ConfigPath (Join-Path $PSScriptRoot '..\..\ops\windows-host' 'jarvis-host.json') -AsJson 2>$null
  if ($statusOutput) { $statusOutput | Set-Content -Path (Join-Path $OutputDir 'status.json') -Force }
} catch {}
# Toolkit manifest
$manifestPath = Join-Path (Resolve-JarvisHostPath -Path $cfg.stateDirectory -BaseDir $env:LOCALAPPDATA) 'tailscale_routes.json'
$manifest = Get-JarvisToolkitManifest -ManifestPath $manifestPath
if ($manifest) { $manifest | ConvertTo-Json -Depth 5 | Set-Content -Path (Join-Path $OutputDir 'tailscale_routes.json') -Force }
# Disk info
$diskInfo = @{ volumes = @() }
$disks = Get-WmiObject Win32_LogicalDisk -Filter "DriveType=3" -ErrorAction SilentlyContinue
if ($disks) {
  $diskInfo.volumes = @()
  foreach ($d in $disks) { $diskInfo.volumes += @{ device = $d.DeviceID; total = $d.Size; free = $d.FreeSpace } }
}
$diskInfo | ConvertTo-Json -Depth 5 | Set-Content -Path (Join-Path $OutputDir 'disk.json') -Force
# Recent toolkit log references (not contents of arbitrary user files)
$logDir = Resolve-JarvisHostPath -Path $cfg.logDirectory -BaseDir $env:LOCALAPPDATA
if (Test-Path $logDir) {
  $logFiles = Get-ChildItem -Path $logDir -File | Where-Object { $_.Name -like '*.log' } | Select-Object -First 5
  $logReferences = @()
  foreach ($lf in $logFiles) { $logReferences += @{ file = $lf.Name; size = $lf.Length; modified = $lf.LastWriteTime.ToString('o') } }
  $logReferences | ConvertTo-Json -Depth 5 | Set-Content -Path (Join-Path $OutputDir 'log_references.json') -Force
}
# Manifest of bundle contents
$bundleFiles = Get-ChildItem -Path $OutputDir -File | Where-Object { $_.Name -notlike '*.zip' }
$bundleManifest = @()
foreach ($f in $bundleFiles) {
  $bundleManifest += @{ file = $f.Name; size = $f.Length; hash = (Get-FileHash -Path $f.FullName -Algorithm SHA256) }
}
$bundleManifest | ConvertTo-Json -Depth 5 | Set-Content -Path (Join-Path $OutputDir 'bundle_manifest.json') -Force
# Explicit exclusion verification
if (Test-Path (Join-Path $OutputDir 'database.db')) { throw "Bundle contains excluded database file" }
if (Test-Path (Join-Path $OutputDir '.env')) { throw "Bundle contains excluded .env file" }
# Archive
$archivePath = $OutputDir + ".zip"
Compress-Archive -Path (Join-Path $OutputDir '*') -DestinationPath $archivePath -Force
# Remove bundle contents after archive to avoid leaving unzipped data (optional, keep for verification)
Write-Output "Sanitized diagnostics archive: $archivePath (files included: $($bundleFiles.Count), exclusions verified)"
