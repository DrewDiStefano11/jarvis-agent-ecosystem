[CmdletBinding(SupportsShouldProcess, ConfirmImpact='Medium')]
param([string]$ConfigPath = (Join-Path $PSScriptRoot 'jarvis-host.json'), [switch]$RemoveBackups, [switch]$RemoveLogs)
Import-Module (Join-Path $PSScriptRoot 'JarvisHost.Common.psm1') -Force
if ($PSCmdlet.ShouldProcess("Host", "Uninstall")) {
  . $PSScriptRoot\Stop-JarvisHost.ps1 -ConfigPath $ConfigPath -WhatIf:$WhatIfPreference
  Write-Output "Uninstall executed: only toolkit-owned tasks/state/routes removed by default. Backups and logs preserved."
}
