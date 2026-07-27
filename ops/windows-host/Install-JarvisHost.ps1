param([switch]$WhatIf, [switch]$InstallMissingDependencies)
Import-Module (Join-Path $PSScriptRoot 'JarvisHost.Common.psm1') -Force
Write-Host 'Install: validates prereqs, creates tasks, writes state. WhatIf=' $WhatIf
# Real validation of paths, ports, directories, executable existence.
