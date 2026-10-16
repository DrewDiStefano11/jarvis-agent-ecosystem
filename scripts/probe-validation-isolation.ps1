# Read-only host probe. Never enables features, starts a guest, or downloads files.
param(
    [ValidateSet('development', 'production', 'unclassified')]
    [string]$DeploymentRole = 'unclassified'
)
$ErrorActionPreference = 'Stop'
$probeResult = [ordered]@{ schemaVersion = '1.0'; deploymentRole = $DeploymentRole; executionEnabled = $false; additionalSpendingCents = 0 }
try {
    $probeOs = Get-CimInstance Win32_OperatingSystem
    $probeMachine = Get-CimInstance Win32_ComputerSystem
    $probeResult.os = [ordered]@{ caption = $probeOs.Caption; version = $probeOs.Version; architecture = $probeOs.OSArchitecture }
    $probeResult.hypervisorPresent = [bool]$probeMachine.HypervisorPresent
    $probeResult.memoryBytes = $probeMachine.TotalPhysicalMemory
    $probeResult.availableMemoryBytes = [long]$probeOs.FreePhysicalMemory * 1024
    $probeResult.logicalCpuCount = $probeMachine.NumberOfLogicalProcessors
    $probeResult.capacityReservationImplemented = $false
} catch { $probeResult.hostDiscovery = 'unavailable' }
$probeResult.features = @()
foreach ($probeFeature in @('Containers-DisposableClientVM', 'Microsoft-Hyper-V-All')) {
    $probeState = 'unknown_requires_administrator_discovery'
    try { $probeState = [string](Get-WindowsOptionalFeature -Online -FeatureName $probeFeature).State } catch { }
    $probeResult.features += [ordered]@{ name = $probeFeature; state = $probeState }
}
$probeResult.sandboxExecutablePresent = Test-Path -LiteralPath "$env:WINDIR\System32\WindowsSandbox.exe"
$probeResult.hyperVModulePresent = [bool](Get-Module -ListAvailable Hyper-V)
$probeResult.services = @()
foreach ($probeService in @('vmms', 'vmcompute')) {
    $probeFound = Get-Service $probeService -ErrorAction SilentlyContinue
    $probeResult.services += [ordered]@{ name = $probeService; present = [bool]$probeFound; state = if ($probeFound) { [string]$probeFound.Status } else { 'absent' } }
}
$probeResult | ConvertTo-Json -Depth 5
