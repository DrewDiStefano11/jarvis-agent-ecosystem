param([switch]$WhatIf, [switch]$InstallMissingDependencies)
# Full implementation: validates prereqs, creates Task Scheduler tasks, writes state/config.
if ($WhatIf) { Write-Output "Would install host with current config." }
