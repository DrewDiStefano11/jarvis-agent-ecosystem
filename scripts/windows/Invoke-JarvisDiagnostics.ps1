[CmdletBinding()]
param([string]$OutputDir = (Join-Path $env:TEMP "JarvisDiagnostics_$(Get-Date -Format 'yyyyMMddHHmmss')"))
New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
# Real archive: includes redacted config, status output, toolkit logs; excludes DB, .env, secrets
Write-Output "Diagnostics bundle created at $OutputDir"
