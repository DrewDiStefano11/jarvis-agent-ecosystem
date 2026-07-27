[CmdletBinding(SupportsShouldProcess, ConfirmImpact='Low')]
param([string]$ConfigPath = (Join-Path $PSScriptRoot 'jarvis-host.json'))
Import-Module (Join-Path $PSScriptRoot 'JarvisHost.Common.psm1') -Force
$cfg = Import-JarvisHostConfig -Path $ConfigPath
$backupDir = Resolve-JarvisHostPath -Path $cfg.backupDirectory -BaseDir $env:LOCALAPPDATA
New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
$tmpFile = Join-Path $env:TEMP "jarvis_backup_tmp_$(Get-Date -Format 'yyyyMMddHHmmss')_" + [System.Guid]::NewGuid().ToString() + ".db"
$tmpScript = $tmpFile + ".py"
try {
  "import sqlite3; src=sqlite3.connect('$($cfg.databasePath)'); dst=sqlite3.connect('$tmpFile'); src.backup(dst); dst.close()" | Set-Content -Path $tmpScript -Force
  $proc = Start-Process -FilePath "python" -ArgumentList $tmpScript -PassThru -Wait -WindowStyle Hidden
  if ($proc.ExitCode -ne 0) { throw "Python backup process failed with exit code $($proc.ExitCode)" }
  $integrity = Test-JarvisSQLiteIntegrity -DatabasePath $tmpFile
  if (-not $integrity) { throw "Backup integrity check failed" }
  $finalName = "jarvis_" + (Get-Date -Format 'yyyyMMddHHmmss') + ".db"
  $finalPath = Join-Path $backupDir $finalName
  Move-Item -Path $tmpFile -Destination $finalPath -Force
  @{ source = $cfg.databasePath; backup = $finalPath; timestamp = (Get-Date -Format 'o'); verified = $true; size = (Get-Item $finalPath).Length } | ConvertTo-Json -Depth 5 | Add-Content -Path (Join-Path $backupDir "backup_metadata.json") -Force
  Write-Output "Backup completed: $finalPath"
} finally {
  if (Test-Path $tmpScript) { Remove-Item $tmpScript -Force -ErrorAction SilentlyContinue }
  if (Test-Path $tmpFile) { Remove-Item $tmpFile -Force -ErrorAction SilentlyContinue }
}
