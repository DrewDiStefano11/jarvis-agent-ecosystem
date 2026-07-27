[CmdletBinding(SupportsShouldProcess, ConfirmImpact='Medium')]
param([string]$ConfigPath = (Join-Path $PSScriptRoot 'jarvis-host.json'), [switch]$RemoveBackups, [switch]$RemoveLogs)
Import-Module (Join-Path $PSScriptRoot 'JarvisHost.Common.psm1') -Force
$cfg = Import-JarvisHostConfig -Path $ConfigPath
if ($PSCmdlet.ShouldProcess("Host", "Uninstall")) {
  . $PSScriptRoot\Stop-JarvisHost.ps1 -ConfigPath $ConfigPath
  $manifest = Get-JarvisToolkitManifest -ManifestPath (Join-Path (Resolve-JarvisHostPath -Path $cfg.stateDirectory -BaseDir $env:LOCALAPPDATA) 'tailscale_routes.json')
  if ($manifest.routes) { foreach ($r in $manifest.routes) { Invoke-JarvisTailscale @("serve", "reset") } }
  $dir = Resolve-JarvisHostPath -Path $cfg.stateDirectory -BaseDir $env:LOCALAPPDATA
  if (Test-Path $dir) { Remove-Item $dir -Recurse -Force -ErrorAction SilentlyContinue }
  Write-Output "Uninstalled: owned tasks/routes/state removed. Database/config/backups/logs preserved unless explicitly removed."
}
