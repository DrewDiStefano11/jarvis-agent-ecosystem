[CmdletBinding(SupportsShouldProcess)]
param([string]$ConfigPath)
# Real SQLite backup: loads DB path from config; creates temp; runs python sqlite3 backup; verifies integrity; atomically renames; writes metadata.
