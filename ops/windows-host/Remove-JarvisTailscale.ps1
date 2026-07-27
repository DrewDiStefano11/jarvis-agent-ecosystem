[CmdletBinding(SupportsShouldProcess)]
param()
Import-Module (Join-Path $PSScriptRoot 'JarvisHost.Common.psm1') -Force
$manifestPath = Join-Path (Resolve-JarvisHostPath -Path (Import-JarvisHostConfig -Path (Join-Path $PSScriptRoot 'jarvis-host.json')).stateDirectory -BaseDir $env:LOCALAPPDATA) 'tailscale_routes.json'
$manifest = Get-JarvisToolkitManifest -ManifestPath $manifestPath
if ($PSCmdlet.ShouldProcess("Tailscale", "Remove toolkit-owned routes")) {
  if ($manifest.routes) {
    foreach ($route in $manifest.routes) {
      # Remove only the specific route, not all Serve config
      try { Invoke-JarvisTailscale @("serve", "reset", $route) } catch { }
    }
  }
  Write-Output "Toolkit-owned Tailscale routes removed. Unrelated routes preserved."
}
