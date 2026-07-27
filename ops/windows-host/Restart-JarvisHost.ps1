[CmdletBinding(SupportsShouldProcess, ConfirmImpact='Medium')]
param([string]$ConfigPath = (Join-Path $PSScriptRoot 'jarvis-host.json'), [switch]$Force)
Import-Module (Join-Path $PSScriptRoot 'JarvisHost.Common.psm1') -Force
$cfg = Import-JarvisHostConfig -Path $ConfigPath
$lockFile = Join-Path (Resolve-JarvisHostPath -Path $cfg.stateDirectory -BaseDir $env:LOCALAPPDATA) 'restart.lock'
try {
  New-JarvisHostLock -LockPath $lockFile
  if ($PSCmdlet.ShouldProcess("Host", "Restart")) {
    $backupDir = Resolve-JarvisHostPath -Path $cfg.backupDirectory -BaseDir $env:LOCALAPPDATA
    $preBackup = Start-Process -FilePath "python" -ArgumentList "-c", "import sqlite3; src=sqlite3.connect('$($cfg.databasePath)'); dst=sqlite3.connect('$backupDir\pre_restart.db'); src.backup(dst); dst.close()" -PassThru -Wait -WindowStyle Hidden
    . $PSScriptRoot\Stop-JarvisHost.ps1 -ConfigPath $ConfigPath -Force:$Force
    Start-Sleep -Seconds 2
    . $PSScriptRoot\Start-JarvisHost.ps1 -ConfigPath $ConfigPath
    Write-Output "Restart completed."
  }
} finally {
  Remove-JarvisHostLock -LockPath $lockFile
}
