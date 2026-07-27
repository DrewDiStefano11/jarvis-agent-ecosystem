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
  $pyContent = "import sqlite3; src=sqlite3.connect('$($cfg.databasePath)'); dst=sqlite3.connect('$tmpFile'); src.backup(dst); dst.close()"
  $pyContent | Set-Content -Path $tmpScript -Force
  $proc = Start-Process -FilePath "python" -ArgumentList $tmpScript -PassThru -Wait -WindowStyle Hidden -RedirectStandardOutput (Join-Path $env:TEMP ("backup_stdout_" + [System.Guid]::NewGuid().ToString() + ".txt")) -RedirectStandardError (Join-Path $env:TEMP ("backup_stderr_" + [System.Guid]::NewGuid().ToString() + ".txt"))
  if ($proc.ExitCode -ne 0) { throw "Python backup process exited with code $($proc.ExitCode)" }
  $integrity = Test-JarvisSQLiteIntegrity -DatabasePath $tmpFile
  if (-not $integrity) { throw "Backup integrity check returned false" }
  $finalName = "jarvis_" + (Get-Date -Format 'yyyyMMddHHmmss') + ".db"
  $finalPath = Join-Path $backupDir $finalName
  Move-Item -Path $tmpFile -Destination $finalPath -Force
  $metadata = @{ source = $cfg.databasePath; backup = $finalPath; timestamp = (Get-Date -Format 'o'); verified = $integrity; pythonExitCode = $proc.ExitCode }
  $metaPath = Join-Path $backupDir ("metadata_" + (Get-Date -Format 'yyyyMMddHHmmss') + ".json")
  $metadata | ConvertTo-Json -Depth 5 | Set-Content -Path $metaPath -Force
  Write-Output "Backup verified: $finalPath (integrity=$integrity, pythonExit=$($proc.ExitCode))"
} finally {
  if (Test-Path $tmpScript) { Remove-Item $tmpScript -Force -ErrorAction SilentlyContinue }
  if (Test-Path $tmpFile) { Remove-Item $tmpFile -Force -ErrorAction SilentlyContinue }
}
