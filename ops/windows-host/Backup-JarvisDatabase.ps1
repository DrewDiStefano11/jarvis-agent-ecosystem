[CmdletBinding(SupportsShouldProcess, ConfirmImpact='Low')]
param([string]$ConfigPath = (Join-Path $PSScriptRoot 'jarvis-host.json'))
Import-Module (Join-Path $PSScriptRoot 'JarvisHost.Common.psm1') -Force
$cfg = Import-JarvisHostConfig -Path $ConfigPath
if ($PSCmdlet.ShouldProcess("Database", "Backup using sqlite3 backup API")) {
  $tmp = Join-Path $cfg.backupDirectory "backup_tmp_$(Get-Date -Format 'yyyyMMddHHmmss').db"
  $pyScript = "$env:TEMP\sqlite_backup.py"
  "import sqlite3; src=sqlite3.connect('$($cfg.databasePath)'); dst=sqlite3.connect('$tmp'); src.backup(dst); dst.close()" | Set-Content $pyScript
  python $pyScript 2>&1 | Out-Null
  $integrity = Test-JarvisSQLiteIntegrity -DatabasePath $tmp
  if ($integrity) {
    $final = Join-Path $cfg.backupDirectory "jarvis_$(Get-Date -Format 'yyyyMMddHHmmss').db"
    Move-Item -Path $tmp -Destination $final -Force
    Write-Output "Backup verified and moved to $final"
  } else {
    Remove-Item -Path $tmp -Force -ErrorAction SilentlyContinue
    throw "Backup integrity check failed"
  }
}
