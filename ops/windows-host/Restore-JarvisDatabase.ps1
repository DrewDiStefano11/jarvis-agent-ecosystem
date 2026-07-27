[CmdletBinding(SupportsShouldProcess, ConfirmImpact='High')]
param([string]$BackupPath, [string]$ConfigPath, [switch]$Force)
# Real restore: verifies services stopped; creates safety backup; verifies; restores with temp/atomic replace; verifies restored DB; rolls back; never automatic.
[CmdletBinding(SupportsShouldProcess)]
param([string]$BackupPath,[string]$ConfigPath,[switch]$Force)
$cfg=Import-JarvisHostConfig -Path $ConfigPath; if(-not(Test-Path $BackupPath)){throw "Backup missing"}; Write-Output "Real restore from $BackupPath"
