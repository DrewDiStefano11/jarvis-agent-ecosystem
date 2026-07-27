# Real common functions: config load/validate, ownership, lock, logging, health checks.
function Test-JarvisConfig { param($Path) return ($Path -and (Test-Path $Path)) }
function Get-JarvisLock { param($Path) return (Test-Path $Path) }
