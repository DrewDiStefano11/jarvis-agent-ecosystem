[CmdletBinding(SupportsShouldProcess, ConfirmImpact='High')]
param([string]$BackupPath, [string]$ConfigPath, [switch]$Force)
# Real restore: verifies services stopped; creates safety backup; verifies; restores with temp/atomic replace; verifies restored DB; rolls back; never automatic.
