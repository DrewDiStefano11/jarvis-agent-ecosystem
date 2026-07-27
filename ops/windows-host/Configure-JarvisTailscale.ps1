[CmdletBinding(SupportsShouldProcess)]
param([string]$ConfigPath = (Join-Path $PSScriptRoot 'jarvis-host.json'))
Import-Module (Join-Path $PSScriptRoot 'JarvisHost.Common.psm1') -Force
$cfg = Import-JarvisHostConfig -Path $ConfigPath
$exe = Get-JarvisTailscaleExecutable
if (-not $exe) { throw "Tailscale CLI not found" }
if ($PSCmdlet.ShouldProcess("Tailscale", "Configure Serve")) {
  Invoke-JarvisTailscale @("serve", "--bg", "--https=443", $cfg.tailscaleServeLocalTarget) 2>&1 | Out-Null
  Write-JarvisToolkitManifest -ManifestPath (Join-Path $cfg.stateDirectory 'tailscale.json') -Data @{ routes = @($cfg.tailscaleServeHostnames); target = $cfg.tailscaleServeLocalTarget }
  Write-Output "Tailscale Serve configured for $cfg.tailscaleServeHostnames"
}
