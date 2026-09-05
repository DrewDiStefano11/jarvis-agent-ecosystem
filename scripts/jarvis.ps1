[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('start', 'stop', 'restart', 'status', 'doctor', 'backup', 'autostart')]
    [string]$Command = 'status',

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$CommandArguments
)

$ErrorActionPreference = 'Stop'
$repository = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repository 'apps\api\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Jarvis API virtual environment was not found at '$python'. Complete Fresh Windows setup first."
}

$apiDirectory = Join-Path $repository 'apps\api'
Push-Location -LiteralPath $apiDirectory
try {
    & $python -m app.runtime_supervisor --repository $repository $Command @CommandArguments
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
