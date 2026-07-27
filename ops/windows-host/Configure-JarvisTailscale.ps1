[CmdletBinding(SupportsShouldProcess)]
param([string]$ConfigPath = (Join-Path $PSScriptRoot 'jarvis-host.json'))
Import-Module (Join-Path $PSScriptRoot 'JarvisHost.Common.psm1') -Force
$cfg = Import-JarvisHostConfig -Path $ConfigPath
$exe = Get-JarvisTailscaleExecutable
if (-not $exe) { throw "Tailscale executable not found" }
# Non-mutating status check
$statusCode = Invoke-JarvisTailscale @("status")
if ($LASTEXITCODE -ne 0 -and $statusCode -ne 0) { throw "Tailscale service not running or not authenticated" }
# Capture existing routes non-mutating
$existingRoutesOutput = Invoke-JarvisTailscale @("serve", "status") 2>&1 | Out-String
if ($PSCmdlet.ShouldProcess("Tailscale", "Configure Serve for $cfg.tailscaleServeHostnames")) {
  # Only apply toolkit routes; do not reset unrelated
  Invoke-JarvisTailscale @("serve", "--https=443", $cfg.tailscaleServeLocalTarget)
  $routes = @($cfg.tailscaleServeHostnames)
  Write-JarvisToolkitManifest -ManifestPath (Join-Path (Resolve-JarvisHostPath -Path $cfg.stateDirectory -BaseDir $env:LOCALAPPDATA) 'tailscale_routes.json') -Data @{ hostnames = $routes; target = $cfg.tailscaleServeLocalTarget; configured = (Get-Date -Format 'o'); version = $exe }
  Write-Output "Tailscale Serve configured. Existing unrelated routes preserved."
}
