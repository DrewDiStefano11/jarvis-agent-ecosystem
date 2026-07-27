[CmdletBinding(SupportsShouldProcess, ConfirmImpact='Medium')]
param([string]$ConfigPath = (Join-Path $PSScriptRoot 'jarvis-host.json'), [switch]$Force)
Import-Module (Join-Path $PSScriptRoot 'JarvisHost.Common.psm1') -Force
$cfg = Import-JarvisHostConfig -Path $ConfigPath
$lockFile = Join-Path (Resolve-JarvisHostPath -Path $cfg.stateDirectory -BaseDir $env:LOCALAPPDATA) 'restart.lock'
$lockHandle = $null
try {
  $lockHandle = New-JarvisHostLock -LockPurpose "restart"
  if ($PSCmdlet.ShouldProcess("Host", "Restart")) {
    $backupDir = Resolve-JarvisHostPath -Path $cfg.backupDirectory -BaseDir $env:LOCALAPPDATA
    New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
    $preBackup = $cfg.databasePath + ".pre_" + (Get-Date -Format 'yyyyMMddHHmmss')
    Copy-Item -Path $cfg.databasePath -Destination $preBackup -Force -ErrorAction SilentlyContinue
    . $PSScriptRoot\Stop-JarvisHost.ps1 -ConfigPath $ConfigPath -Force:$Force
    Start-Sleep -Seconds 2
    . $PSScriptRoot\Start-JarvisHost.ps1 -ConfigPath $ConfigPath
    Write-Output "Restart completed. Pre-restart safety backup: $preBackup"
  }
} finally {
  if ($lockHandle) { Remove-JarvisHostLock -LockHandle $lockHandle }
}
