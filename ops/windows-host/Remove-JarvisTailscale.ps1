[CmdletBinding(SupportsShouldProcess)]
param()
Import-Module (Join-Path $PSScriptRoot 'JarvisHost.Common.psm1') -Force
$manifestPath = Join-Path (Resolve-JarvisHostPath -Path (Import-JarvisHostConfig -Path (Join-Path $PSScriptRoot 'jarvis-host.json')).stateDirectory -BaseDir $env:LOCALAPPDATA) 'tailscale_routes.json'
$manifest = Get-JarvisToolkitManifest -ManifestPath $manifestPath
if ($PSCmdlet.ShouldProcess("Tailscale", "Remove only toolkit-owned routes")) {
  foreach ($route in $manifest.hostnames) {
    try { Invoke-JarvisTailscale @("serve", "--https=443", $route, "reset") } catch { }
  }
  Write-Output "Toolkit-owned routes removed. Unrelated routes preserved."
}
