[CmdletBinding(SupportsShouldProcess)]
param([string]$ConfigPath = (Join-Path $PSScriptRoot 'jarvis-host.json'))
Import-Module (Join-Path $PSScriptRoot 'JarvisHost.Common.psm1') -Force
if (-not (Test-Path $ConfigPath)) { throw "Config missing: $ConfigPath" }
$cfg = Import-JarvisHostConfig -Path $ConfigPath
if ($cfg.repoPath -and -not (Test-Path $cfg.repoPath)) { throw "Repo path missing: $cfg.repoPath" }
if ($cfg.backendPort -le 0 -or $cfg.backendPort -gt 65535) { throw "Invalid backend port" }
Write-JarvisHostLog -LogDir ($cfg.logDirectory) -FileName "install" -Message "Installation started"
if ($PSCmdlet.ShouldProcess("Host", "Create directories and tasks for $ConfigPath")) {
  if ($cfg.logDirectory) { New-Item -ItemType Directory -Path $cfg.logDirectory -Force | Out-Null }
  if ($cfg.stateDirectory) { New-Item -ItemType Directory -Path (Resolve-JarvisHostPath -Path $cfg.stateDirectory -BaseDir $env:LOCALAPPDATA) -Force | Out-Null }
  Write-Output "Installation executed: directories created, tasks would be registered. Config: $ConfigPath"
}
