[CmdletBinding(SupportsShouldProcess)]
param()
Import-Module (Join-Path $PSScriptRoot 'JarvisHost.Common.psm1') -Force
$manifest = Get-JarvisToolkitManifest -ManifestPath (Join-Path $env:LOCALAPPDATA 'JarvisHost' 'tailscale.json')
if ($PSCmdlet.ShouldProcess("Tailscale", "Remove toolkit routes")) {
  if ($manifest.routes) {
    foreach ($route in $manifest.routes) {
      Invoke-JarvisTailscale @("serve", "reset") 2>&1 | Out-Null
    }
  }
  Write-Output "Toolkit-owned Tailscale routes removed."
}
