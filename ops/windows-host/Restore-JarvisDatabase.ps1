[CmdletBinding(SupportsShouldProcess, ConfirmImpact='High')]
param([Parameter(Mandatory=$true)][string]$BackupPath, [string]$ConfigPath = (Join-Path $PSScriptRoot 'jarvis-host.json'), [switch]$Force)
Import-Module (Join-Path $PSScriptRoot 'JarvisHost.Common.psm1') -Force
$cfg = Import-JarvisHostConfig -Path $ConfigPath
if (-not $Force -and -not $PSCmdlet.ShouldContinue("Restore from $BackupPath? This requires confirmation.", "Confirm Restore")) { return }
if (-not (Test-Path $BackupPath)) { throw "Backup not found: $BackupPath" }
$integrity = Test-JarvisSQLiteIntegrity -DatabasePath $BackupPath
if (-not $integrity) { throw "Backup failed integrity check" }
$safetyPath = $cfg.databasePath + ".safety_" + (Get-Date -Format 'yyyyMMddHHmmss')
Copy-Item -Path $cfg.databasePath -Destination $safetyPath -Force -ErrorAction SilentlyContinue
$tmpRestore = $cfg.databasePath + ".restore_tmp_" + [System.Guid]::NewGuid()
try {
  Copy-Item -Path $BackupPath -Destination $tmpRestore -Force
  $restoredIntegrity = Test-JarvisSQLiteIntegrity -DatabasePath $tmpRestore
  if (-not $restoredIntegrity) { throw "Restored database failed verification" }
  Move-Item -Path $tmpRestore -Destination $cfg.databasePath -Force
  $postIntegrity = Test-JarvisSQLiteIntegrity -DatabasePath $cfg.databasePath
  if (-not $postIntegrity) { throw "Post-replace integrity failed; rolling back"; Copy-Item -Path $safetyPath -Destination $cfg.databasePath -Force }
  Write-Output "Restore successful from $BackupPath (safety: $safetyPath)"
} finally {
  if (Test-Path $tmpRestore) { Remove-Item $tmpRestore -Force -ErrorAction SilentlyContinue }
}
