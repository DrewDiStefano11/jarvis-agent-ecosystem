# Explicit operator procedure: creates only a stopped VM. Never installs features,
# downloads an image, starts candidate code, or changes global Hyper-V settings.
param(
    [Parameter(Mandatory)][string]$PlanPath,
    [Parameter(Mandatory)][ValidatePattern('^[a-f0-9]{64}$')][string]$ExpectedPlanSha256,
    [switch]$ApproveProvisioning
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
function Assert-LocalPath([string]$Value, [bool]$Directory) {
    if ($Value -notmatch '^[A-Za-z]:\\' -or $Value.StartsWith('\\') -or
        [IO.Path]::GetFullPath($Value) -cne $Value) { throw 'noncanonical local path' }
    $current = Get-Item -LiteralPath $Value -Force
    if ([bool]$current.PSIsContainer -ne $Directory) { throw 'wrong path kind' }
    while ($current) {
        if ($current.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'reparse path' }
        $current = if ($current.PSIsContainer) { $current.Parent } else { $current.Directory }
    }
}
if (-not $ApproveProvisioning) { throw 'Explicit operator approval is required; no effects performed.' }
Assert-LocalPath $PlanPath $false
$planStream = [IO.File]::Open($PlanPath, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
try {
    if ($planStream.Length -gt 16384) { throw 'plan limit' }
    $planBytes = [byte[]]::new([int]$planStream.Length)
    $readTotal = 0
    while ($readTotal -lt $planBytes.Length) {
        $readCount = $planStream.Read($planBytes, $readTotal, $planBytes.Length - $readTotal)
        if (-not $readCount) { throw 'incomplete plan' }
        $readTotal += $readCount
    }
    $planHasher = [Security.Cryptography.SHA256]::Create()
    try { $planHash = ([BitConverter]::ToString($planHasher.ComputeHash($planBytes))).Replace('-','').ToLowerInvariant() }
    finally { $planHasher.Dispose() }
    if ($planHash -cne $ExpectedPlanSha256) { throw 'approved plan bytes changed' }
    $plan = [Text.Encoding]::UTF8.GetString($planBytes) | ConvertFrom-Json
} finally { $planStream.Dispose() }
$required = @('schemaVersion','hostMachineGuid','storageRoot','imagePath','imageSha256',
              'vmName','ownershipDigest','memoryBytes','cpuCount','diskBytes',
              'existingImageLicenseConfirmed','additionalSpendingCents')
$actualFields = @($plan.PSObject.Properties.Name | Sort-Object) -join ','
$expectedFields = @($required | Sort-Object) -join ','
if ($actualFields -cne $expectedFields) {
    throw 'unexpected plan fields'
}
foreach ($numericField in @('memoryBytes','cpuCount','diskBytes','additionalSpendingCents')) {
    if ($plan.$numericField.GetType().Name -notin @('Int32','Int64')) { throw 'integer plan field required' }
}
if ($plan.existingImageLicenseConfirmed.GetType().Name -ne 'Boolean') { throw 'explicit license confirmation required' }
if ($plan.schemaVersion -cne '1.0' -or $plan.vmName -cnotmatch '^jarvis-validation-[a-f0-9]{32}$' -or
    $plan.ownershipDigest -cnotmatch '^[a-f0-9]{64}$' -or $plan.imageSha256 -cnotmatch '^[a-f0-9]{64}$' -or
    $plan.existingImageLicenseConfirmed -ne $true -or $plan.additionalSpendingCents -ne 0 -or
    $plan.memoryBytes -lt 2147483648 -or $plan.memoryBytes -gt 8589934592 -or
    $plan.cpuCount -lt 1 -or $plan.cpuCount -gt 4 -or
    $plan.diskBytes -lt 1073741824 -or $plan.diskBytes -gt 68719476736) { throw 'plan policy mismatch' }
$actualHost = (Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Cryptography').MachineGuid
if ($plan.hostMachineGuid -cne $actualHost) { throw 'approval belongs to a different host' }
Assert-LocalPath $plan.storageRoot $true
Assert-LocalPath $plan.imagePath $false
if ([IO.Path]::GetExtension($plan.imagePath) -cne '.vhdx') { throw 'approved VHDX required' }
# The operator must pre-create a dedicated service-owned root; never use a user
# workspace/profile. This procedure neither creates nor relaxes its ACLs.
$rootMarker = Join-Path $plan.storageRoot '.jarvis-runner-root.json'
Assert-LocalPath $rootMarker $false
if ((Get-Content -LiteralPath $rootMarker -Raw | ConvertFrom-Json).hostMachineGuid -cne $actualHost) {
    throw 'unmarked storage root'
}
$env:PSModulePath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\Modules"
Import-Module Hyper-V -ErrorAction Stop
if ((Get-Service vmms).Status -ne 'Running') { throw 'Hyper-V unavailable; do not auto-enable it' }
if (Get-VM -Name $plan.vmName -ErrorAction SilentlyContinue) { throw 'existing VM requires recovery' }
$freeMemory = [long](Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory * 1024
if ($freeMemory -lt ([long]$plan.memoryBytes + 536870912)) { throw 'insufficient current memory' }
if ((Get-CimInstance Win32_ComputerSystem).NumberOfLogicalProcessors -lt $plan.cpuCount) { throw 'insufficient CPU' }
$volume = [IO.DriveInfo]::new([IO.Path]::GetPathRoot($plan.storageRoot))
if ($volume.AvailableFreeSpace -lt ([long]$plan.diskBytes + 1073741824)) { throw 'insufficient current disk space' }
$base = Get-VHD -Path $plan.imagePath
if ($base.Size -gt $plan.diskBytes -or $base.ParentPath) { throw 'unsupported parent disk' }
$image = [IO.File]::Open($plan.imagePath, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
try {
    if ($image.Length -gt 68719476736) { throw 'image size limit' }
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $actualHash = ([BitConverter]::ToString($sha.ComputeHash($image))).Replace('-','').ToLowerInvariant() }
    finally { $sha.Dispose() }
    if ($actualHash -cne $plan.imageSha256) { throw 'image bytes changed' }
    $vmRoot = Join-Path $plan.storageRoot $plan.vmName
    if (Test-Path -LiteralPath $vmRoot) { throw 'existing storage requires recovery' }
    # Creation can leave an uncertain stopped VM/disk after interruption. Never
    # blindly repeat or recursively remove it; retain the exact name for recovery.
    New-Item -ItemType Directory -Path $vmRoot | Out-Null
    $child = Join-Path $vmRoot 'guest.vhdx'
    New-VHD -Path $child -ParentPath $plan.imagePath -Differencing | Out-Null
    $vm = New-VM -Name $plan.vmName -Generation 2 -MemoryStartupBytes $plan.memoryBytes -VHDPath $child -Path $vmRoot
    Set-VM -VM $vm -StaticMemory -ProcessorCount $plan.cpuCount -AutomaticStartAction Nothing -AutomaticStopAction TurnOff -AutomaticCheckpointsEnabled $false -CheckpointType Disabled -Notes "jarvis-validation-v1:$($plan.ownershipDigest)"
    Get-VMNetworkAdapter -VM $vm | Remove-VMNetworkAdapter -Confirm:$false
    Get-VMIntegrationService -VM $vm | Disable-VMIntegrationService
    Enable-VMResourceMetering -VM $vm
    $vm = Get-VM -Id $vm.Id
    if ([string]$vm.State -ne 'Off' -or @(Get-VMNetworkAdapter -VM $vm).Count) { throw 'provisioning verification failed' }
    @{ vmId = [string]$vm.Id; ownershipDigest = $plan.ownershipDigest; state = 'off';
       executionEnabled = $false; additionalSpendingCents = 0; storageCleanupComplete = $false } | ConvertTo-Json -Compress
} finally { $image.Dispose() }
