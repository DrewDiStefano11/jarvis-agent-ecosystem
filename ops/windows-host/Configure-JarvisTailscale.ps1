[CmdletBinding(SupportsShouldProcess)]
param([string]$ConfigPath = (Join-Path $PSScriptRoot 'jarvis-host.json'))
Import-Module (Join-Path $PSScriptRoot 'JarvisHost.Common.psm1') -Force
$cfg = Import-JarvisHostConfig -Path $ConfigPath
$exe = Get-JarvisTailscaleExecutable
if (-not $exe) { throw "Tailscale executable not found" }
$status = Invoke-JarvisTailscale @("status")
if ($LASTEXITCODE -ne 0) { throw "Tailscale not authenticated or service not running" }
$existing = Invoke-JarvisTailscale @("serve", "--https=443", $cfg.tailscaleServeLocalTarget) 2>&1 | Out-String
if ($PSCmdlet.ShouldProcess("Tailscale", "Configure Serve")) {
  Write-JarvisToolkitManifest -ManifestPath (Join-Path (Resolve-JarvisHostPath -Path $cfg.stateDirectory -BaseDir $env:LOCALAPPDATA) 'tailscale_routes.json') -Data @{ hostnames = @($cfg.tailscaleServeHostnames); target = $cfg.tailscaleServeLocalTarget; configured = (Get-Date -Format 'o') }
  Write-Output "Tailscale Serve configured. Existing routes preserved."
}
