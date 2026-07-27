[CmdletBinding(SupportsShouldProcess, ConfirmImpact='High')]
param([string]$BackupPath, [string]$ConfigPath = (Join-Path $PSScriptRoot 'jarvis-host.json'), [switch]$Force)
Import-Module (Join-Path $PSScriptRoot 'JarvisHost.Common.psm1') -Force
if (-not $BackupPath) { throw "BackupPath required" }
$cfg = Import-JarvisHostConfig -Path $ConfigPath
if (-not (Test-Path $BackupPath)) { throw "Backup not found: $BackupPath" }
$integrity = Test-JarvisSQLiteIntegrity -DatabasePath $BackupPath
if (-not $integrity) { throw "Backup failed integrity check" }
if ($PSCmdlet.ShouldProcess("Database", "Restore from $BackupPath")) {
  $safety = "$($cfg.databasePath).safety_$(Get-Date -Format 'yyyyMMddHHmmss')"
  Copy-Item -Path $cfg.databasePath -Destination $safety -Force -ErrorAction SilentlyContinue
  Copy-Item -Path $BackupPath -Destination $cfg.databasePath -Force
  Write-Output "Restored database from $BackupPath (safety backup: $safety)"
}
