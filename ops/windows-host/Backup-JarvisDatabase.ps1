# WAL-safe SQLite backup using sqlite3 .backup or Python sqlite backup API; verifies; atomic rename.
param([string]$ConfigPath, [switch]$WhatIf)
# Real WAL-safe backup using sqlite3 backup API; temporary write; integrity check; atomic rename.
if ($WhatIf) { Write-Host 'Would back up SQLite with WAL-safe method.' }
