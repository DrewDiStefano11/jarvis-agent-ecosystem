[CmdletBinding(SupportsShouldProcess, ConfirmImpact='Medium')]
param([string]$ConfigPath = (Join-Path $PSScriptRoot 'jarvis-host.json'), [switch]$RemoveBackups, [switch]$RemoveLogs)
Import-Module (Join-Path $PSScriptRoot 'JarvisHost.Common.psm1') -Force
$cfg = Import-JarvisHostConfig -Path $ConfigPath
if ($PSCmdlet.ShouldProcess("Host", "Uninstall")) {
  . $PSScriptRoot\Stop-JarvisHost.ps1 -ConfigPath $ConfigPath -Force:$false
  # Remove toolkit-owned scheduled tasks by name pattern if they exist
  $taskPrefix = "JarvisHost"
  $existingTasks = Get-ScheduledTask | Where-Object { $_.TaskName -like "*$taskPrefix*" }
  foreach ($task in $existingTasks) {
    Unregister-ScheduledTask -TaskName $task.TaskName -Confirm:$false -ErrorAction SilentlyContinue
  }
  # Remove owned Tailscale routes
  $manifestPath = Join-Path (Resolve-JarvisHostPath -Path $cfg.stateDirectory -BaseDir $env:LOCALAPPDATA) 'tailscale_routes.json'
  $manifest = Get-JarvisToolkitManifest -ManifestPath $manifestPath
  if ($manifest.routes) {
    foreach ($route in $manifest.routes) {
      try { Invoke-JarvisTailscale @("serve", "reset", $route) } catch { }
    }
  }
  # Remove runtime locks and temporary state
  $stateDir = Resolve-JarvisHostPath -Path $cfg.stateDirectory -BaseDir $env:LOCALAPPDATA
  if (Test-Path $stateDir) {
    $files = Get-ChildItem -Path $stateDir | Where-Object { $_.Name -match '\.(lock|tmp|json)$' }
    foreach ($f in $files) { Remove-Item $f.FullName -Force -ErrorAction SilentlyContinue }
  }
  # Preserve config, logs, backups, database by default
  if ($RemoveBackups -and $cfg.backupDirectory) {
    $backupDir = Resolve-JarvisHostPath -Path $cfg.backupDirectory -BaseDir $env:LOCALAPPDATA
    if (Test-Path $backupDir) { Remove-Item $backupDir -Recurse -Force -ErrorAction SilentlyContinue }
  }
  if ($RemoveLogs -and $cfg.logDirectory) {
    $logDir = Resolve-JarvisHostPath -Path $cfg.logDirectory -BaseDir $env:LOCALAPPDATA
    if (Test-Path $logDir) { Remove-Item $logDir -Recurse -Force -ErrorAction SilentlyContinue }
  }
  Write-Output "Uninstall completed. Owned tasks/routes removed. Config/DB/backups/logs preserved unless explicitly removed."
}
