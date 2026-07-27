[CmdletBinding(SupportsShouldProcess, ConfirmImpact='High')]
param([Parameter(Mandatory=$true)][string]$BackupPath, [string]$ConfigPath = (Join-Path $PSScriptRoot 'jarvis-host.json'), [switch]$Force)
Import-Module (Join-Path $PSScriptRoot 'JarvisHost.Common.psm1') -Force
if (-not $Force -and -not $PSCmdlet.ShouldContinue("Restore database from $BackupPath? Services must be stopped. Confirm?", "Confirm Restore")) { return }
if (-not (Test-Path $BackupPath)) { throw "Backup not found: $BackupPath" }
$cfg = Import-JarvisHostConfig -Path $ConfigPath
# Verify services stopped by checking process ownership
$backMeta = Read-JarvisProcessMetadata -Path (Join-Path $cfg.stateDirectory 'backend.json')
$frontMeta = Read-JarvisProcessMetadata -Path (Join-Path $cfg.stateDirectory 'frontend.json')
if ($backMeta.pid) {
  $verified = Test-JarvisProcessOwnership -PID ([int]$backMeta.pid) -ExpectedExecutable $backMeta.executable -ExpectedWorkingDir $backMeta.workingDirectory -ExpectedArguments $backMeta.arguments -ExpectedStartTime ([datetime]::Parse($backMeta.processStartTime))
  if ($verified) { throw "Backend service is still running. Stop before restore." }
}
if ($frontMeta.pid) {
  $verified = Test-JarvisProcessOwnership -PID ([int]$frontMeta.pid) -ExpectedExecutable $frontMeta.executable -ExpectedWorkingDir $frontMeta.workingDirectory -ExpectedArguments $frontMeta.arguments -ExpectedStartTime ([datetime]::Parse($frontMeta.processStartTime))
  if ($verified) { throw "Frontend service is still running. Stop before restore." }
}
$integrity = Test-JarvisSQLiteIntegrity -DatabasePath $BackupPath
if (-not $integrity) { throw "Backup failed integrity check before restore" }
$safetyPath = $cfg.databasePath + ".safety_" + (Get-Date -Format 'yyyyMMddHHmmss')
try {
  if (Test-Path $cfg.databasePath) {
    Copy-Item -Path $cfg.databasePath -Destination $safetyPath -Force -ErrorAction SilentlyContinue
    $safetyIntegrity = Test-JarvisSQLiteIntegrity -DatabasePath $safetyPath
    if (-not $safetyIntegrity) { throw "Safety backup failed integrity check; aborting restore" }
  }
  $tmpRestore = $cfg.databasePath + ".restore_tmp_" + [System.Guid]::NewGuid()
  Copy-Item -Path $BackupPath -Destination $tmpRestore -Force
  $restoredIntegrity = Test-JarvisSQLiteIntegrity -DatabasePath $tmpRestore
  if (-not $restoredIntegrity) { throw "Restored temporary database failed verification" }
  # Handle WAL and SHM safely
  $dbName = [System.IO.Path]::GetFileName($cfg.databasePath)
  $dbDir = Split-Path $cfg.databasePath
  $walFile = Join-Path $dbDir ($dbName + "-wal")
  $shmFile = Join-Path $dbDir ($dbName + "-shm")
  if (Test-Path $cfg.databasePath) { Remove-Item $cfg.databasePath -Force -ErrorAction SilentlyContinue }
  if (Test-Path $walFile) { Remove-Item $walFile -Force -ErrorAction SilentlyContinue }
  if (Test-Path $shmFile) { Remove-Item $shmFile -Force -ErrorAction SilentlyContinue }
  Move-Item -Path $tmpRestore -Destination $cfg.databasePath -Force
  $postIntegrity = Test-JarvisSQLiteIntegrity -DatabasePath $cfg.databasePath
  if (-not $postIntegrity) {
    # Roll back to safety backup
    if (Test-Path $safetyPath) {
      Remove-Item $cfg.databasePath -Force -ErrorAction SilentlyContinue
      Copy-Item -Path $safetyPath -Destination $cfg.databasePath -Force
    }
    throw "Post-replace verification failed. Rolled back to safety backup: $safetyPath"
  }
  Write-Output "Restore successful from $BackupPath. Safety backup: $safetyPath"
} finally {
  if (Test-Path $tmpRestore) { Remove-Item $tmpRestore -Force -ErrorAction SilentlyContinue }
}
