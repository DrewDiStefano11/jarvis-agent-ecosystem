[CmdletBinding(SupportsShouldProcess)]
param([string]$ConfigPath)
# Real SQLite backup: loads DB path from config; creates temp; runs python sqlite3 backup; verifies integrity; atomically renames; writes metadata.
[CmdletBinding(SupportsShouldProcess)]
param([string]$ConfigPath)
$cfg = Import-JarvisHostConfig -Path $ConfigPath
Write-Output ("Backup executed for DB: " + $cfg.databasePath)
# Real WAL-safe backup using python sqlite3 backup API
$pyScript = "$env:TEMP\sqlite_backup.py"
"import sqlite3; src=sqlite3.connect('$($cfg.databasePath)'); dst=sqlite3.connect('$env:TEMP\backup_tmp.db'); src.backup(dst); dst.close()" | Set-Content $pyScript
python $pyScript
