[CmdletBinding(SupportsShouldProcess, ConfirmImpact='Low')]
param([string]$ConfigPath = (Join-Path $PSScriptRoot 'jarvis-host.json'))
Import-Module (Join-Path $PSScriptRoot 'JarvisHost.Common.psm1') -Force
$cfg = Import-JarvisHostConfig -Path $ConfigPath
$backupDir = Resolve-JarvisHostPath -Path $cfg.backupDirectory -BaseDir $env:LOCALAPPDATA
if (-not $PSCmdlet.ShouldProcess("Database", "Backup to $backupDir")) { return }
New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
$tmpFile = Join-Path $env:TEMP ("jarvis_backup_tmp_" + (Get-Date -Format 'yyyyMMddHHmmss') + "_" + [System.Guid]::NewGuid().ToString() + ".db")
$tmpScript = $tmpFile + ".py"
try {
  $dbQuoted = $cfg.databasePath -replace '"', '""'
  $pyContent = "import sqlite3; src=sqlite3.connect('" + $dbQuoted + "'); dst=sqlite3.connect('" + ($tmpFile -replace '"', '""') + "'); src.backup(dst); dst.close()"
  $pyContent | Set-Content -Path $tmpScript -Force
  $proc = Start-Process -FilePath "python" -ArgumentList $tmpScript -PassThru -Wait -WindowStyle Hidden -RedirectStandardOutput (Join-Path $env:TEMP ("bk_stdout_" + [System.Guid]::NewGuid().ToString())) -RedirectStandardError (Join-Path $env:TEMP ("bk_stderr_" + [System.Guid]::NewGuid().ToString()))
  if ($proc.ExitCode -ne 0) { throw "Python backup exit code: $($proc.ExitCode)" }
  $integrity = Test-JarvisSQLiteIntegrity -DatabasePath $tmpFile
  if (-not $integrity) { throw "Backup integrity check returned false" }
  $finalName = "jarvis_" + (Get-Date -Format 'yyyyMMddHHmmss') + ".db"
  $finalPath = Join-Path $backupDir $finalName
  Move-Item -Path $tmpFile -Destination $finalPath -Force
  @{ source = $cfg.databasePath; backup = $finalPath; timestamp = (Get-Date -Format 'o'); integrity = $integrity; pythonExit = $proc.ExitCode } | ConvertTo-Json -Depth 5 | Set-Content -Path (Join-Path $backupDir "metadata_" + (Get-Date -Format 'yyyyMMddHHmmss') + ".json") -Force
  # Retention: keep only 30 newest toolkit backups
  $allBackups = Get-ChildItem -Path $backupDir -Filter "jarvis_*.db" | Sort-Object LastWriteTime -Descending
  if ($allBackups.Count -gt 30) {
    $allBackups | Select-Object -Skip 30 | ForEach-Object { Remove-Item $_.FullName -Force }
  }
  Write-Output "Backup verified: $finalPath (integrity=$integrity exit=$($proc.ExitCode))"
} finally {
  if (Test-Path $tmpScript) { Remove-Item $tmpScript -Force -ErrorAction SilentlyContinue }
  if (Test-Path $tmpFile) { Remove-Item $tmpFile -Force -ErrorAction SilentlyContinue }
}
