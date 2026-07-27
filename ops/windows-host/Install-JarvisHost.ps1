[CmdletBinding(SupportsShouldProcess)]
param([string]$ConfigPath = (Join-Path $PSScriptRoot 'jarvis-host.json'))
Import-Module (Join-Path $PSScriptRoot 'JarvisHost.Common.psm1') -Force
if (-not (Test-Path $ConfigPath)) { throw "Config missing: $ConfigPath" }
$cfg = Import-JarvisHostConfig -Path $ConfigPath
if ($cfg.repoPath -and -not (Test-Path $cfg.repoPath)) { throw "Repo path missing: $cfg.repoPath" }
if ($cfg.backendPort -le 0 -or $cfg.backendPort -gt 65535) { throw "Invalid backend port" }
if ($PSCmdlet.ShouldProcess("Host", "Install with config $ConfigPath")) {
  $dir = Resolve-JarvisHostPath -Path $cfg.stateDirectory -BaseDir $env:LOCALAPPDATA; New-Item -ItemType Directory -Path $dir -Force | Out-Null
  $dir = Resolve-JarvisHostPath -Path $cfg.logDirectory -BaseDir $env:LOCALAPPDATA; New-Item -ItemType Directory -Path $dir -Force | Out-Null
  $dir = Resolve-JarvisHostPath -Path $cfg.backupDirectory -BaseDir $env:LOCALAPPDATA; New-Item -ItemType Directory -Path $dir -Force | Out-Null
  Write-JarvisToolkitManifest -ManifestPath (Join-Path $dir 'install.json') -Data @{ installed = $true; time = (Get-Date -Format 'o'); config = Redact-JarvisConfig -Config $cfg }
  Write-Output "Installation completed: directories and ownership manifest created."
}
