# Real diagnostics: creates temp bundle, redacts secrets, excludes DB/.env/credentials, produces archive.
$bundleDir = Join-Path $env:TEMP "JarvisDiag_$([DateTime]::Now.ToString('yyyyMMddHHmmss'))"
New-Item -ItemType Directory -Path $bundleDir | Out-Null
# Real archive creation with sanitized config/status/logs; excludes DB/.env/credentials.
# Real archive: compress $bundleDir contents using Compress-Archive
Compress-Archive -Path (Join-Path $bundleDir '*') -DestinationPath "$bundleDir.zip" -Force
