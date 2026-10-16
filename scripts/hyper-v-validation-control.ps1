# Fixed native management/telemetry protocol. No guest commands or result importer.
param(
    [Parameter(Mandatory)][ValidateSet('inspect','stop','unregister')][string]$Operation,
    [Parameter(Mandatory)][Guid]$VmId,
    [Parameter(Mandatory)][ValidatePattern('^[a-f0-9]{64}$')][string]$OwnershipDigest,
    [Parameter(Mandatory)][Guid]$HostMachineGuid
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
try {
    $actualHostGuid = [Guid](Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Cryptography').MachineGuid
    if ($actualHostGuid -ne $HostMachineGuid) { throw 'foreign host' }
    $env:PSModulePath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\Modules"
    Import-Module Hyper-V -ErrorAction Stop
    $nativeVm = Get-VM -Id $VmId -ErrorAction Stop
    if ($nativeVm.Notes -cne "jarvis-validation-v1:$OwnershipDigest") { throw 'owner mismatch' }
    if ($Operation -eq 'stop') {
        Stop-VM -VM $nativeVm -TurnOff -Force -Confirm:$false -ErrorAction Stop
        $nativeVm = Get-VM -Id $VmId -ErrorAction Stop
    }
    if ($Operation -eq 'unregister') {
        if ([string]$nativeVm.State -ne 'Off') { throw 'stop required' }
        Remove-VM -VM $nativeVm -Force -Confirm:$false -ErrorAction Stop
        # A management-service error must not masquerade as verified absence.
        $remainingVm = Get-VM -ErrorAction Stop | Where-Object { $_.Id -eq $VmId }
        if ($remainingVm) { throw 'removal uncertain' }
        @{ absent = $true; vmId = [string]$VmId; ownershipDigest = $OwnershipDigest;
           hostMachineGuid = [string]$actualHostGuid; storageCleanupComplete = $false } | ConvertTo-Json -Compress
        exit 0
    }
    $nativeState = ([string]$nativeVm.State).ToLowerInvariant()
    if ($nativeState -notin @('off','running','paused','saved')) { $nativeState = 'other' }
    $connected = @(Get-VMNetworkAdapter -VM $nativeVm | Where-Object { $_.SwitchId -ne [Guid]::Empty }).Count
    $checkpoints = @(Get-VMSnapshot -VM $nativeVm).Count
    $nativeCpu = Get-VMProcessor -VM $nativeVm
    $nativeMemory = Get-VMMemory -VM $nativeVm
    @{ vmId = [string]$nativeVm.Id; ownershipDigest = $OwnershipDigest;
       hostMachineGuid = [string]$actualHostGuid;
       observedAt = [DateTimeOffset]::UtcNow.ToString('o'); state = $nativeState;
       memoryAssignedBytes = [long]$nativeVm.MemoryAssigned; memoryStartupBytes = [long]$nativeMemory.Startup;
       cpuCount = [int]$nativeCpu.Count; connectedNetworkAdapters = [int]$connected;
       hostVmUptimeSeconds = [long][Math]::Floor($nativeVm.Uptime.TotalSeconds);
       hostCpuUsagePercent = [int]$nativeVm.CPUUsage;
       checkpointCount = [int]$checkpoints; dynamicMemory = [bool]$nativeMemory.DynamicMemoryEnabled;
       automaticStart = if ([string]$nativeVm.AutomaticStartAction -eq 'Nothing') { 'nothing' } else { 'other' }
    } | ConvertTo-Json -Compress
} catch {
    # Native errors may contain host paths/configuration; do not export them.
    [Console]::Error.WriteLine('HYPERV_NATIVE_CONTROL_UNAVAILABLE')
    exit 1
}
