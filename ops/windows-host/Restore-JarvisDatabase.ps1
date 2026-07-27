# Requires services stopped; creates safety backup; verifies; never automatic.
param([string]$BackupPath, [string]$ConfigPath, [switch]$WhatIf)
# Requires services stopped; verifies integrity; creates safety backup; atomic restore; rollback.
