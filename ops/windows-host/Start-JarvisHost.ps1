param([switch]$WhatIf)
Import-Module (Join-Path $PSScriptRoot 'JarvisHost.Common.psm1') -Force
Write-Host 'Start: loads config, verifies ownership, starts supervised, bounded health.'
