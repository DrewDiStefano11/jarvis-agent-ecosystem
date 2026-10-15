# Fixed native management/telemetry protocol. No guest commands or result importer.
param(
    [Parameter(Mandatory)][ValidateSet('inspect','stop','unregister','absence','attach_media','detach_media','inspect_media')][string]$Operation,
    [Parameter(Mandatory)][Guid]$VmId,
    [Parameter(Mandatory)][ValidatePattern('^[a-f0-9]{64}$')][string]$OwnershipDigest,
    [Parameter(Mandatory)][Guid]$HostMachineGuid,
    [string]$MediaPath,
    [ValidatePattern('^[a-f0-9]{64}$')][string]$MediaDigest,
    [long]$MediaBytes
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
function Assert-MediaVmSafety($Vm) {
    if ($Vm.Id -ne $VmId -or $Vm.Notes -cne "jarvis-validation-v1:$OwnershipDigest") { throw 'owner mismatch' }
    if ([string]$Vm.State -ne 'Off' -or $Vm.Generation -ne 2) { throw 'stopped generation 2 required' }
    if (@(Get-VMNetworkAdapter -VM $Vm).Count -or @(Get-VMSnapshot -VM $Vm).Count -or
        @(Get-VMIntegrationService -VM $Vm | Where-Object { $_.Enabled }).Count) { throw 'sharing or network enabled' }
    if ([string]$Vm.AutomaticStartAction -ne 'Nothing' -or (Get-VMMemory -VM $Vm).DynamicMemoryEnabled) { throw 'unsafe startup configuration' }
}
try {
    $actualHostGuid = [Guid](Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Cryptography').MachineGuid
    if ($actualHostGuid -ne $HostMachineGuid) { throw 'foreign host' }
    $env:PSModulePath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\Modules"
    Import-Module Hyper-V -ErrorAction Stop
    if ($Operation -eq 'absence') {
        # An inventory failure cannot be interpreted as absence. Original storage
        # ownership still comes from the protected creation registration, not this
        # echoed digest. Never inspect candidate JSON or suppress native errors.
        $remainingVm = Get-VM -ErrorAction Stop | Where-Object { $_.Id -eq $VmId }
        if ($remainingVm) { throw 'VM still registered' }
        @{ absent = $true; vmId = [string]$VmId; ownershipDigest = $OwnershipDigest;
           hostMachineGuid = [string]$actualHostGuid } | ConvertTo-Json -Compress
        exit 0
    }
    $nativeVm = Get-VM -Id $VmId -ErrorAction Stop
    if ($nativeVm.Notes -cne "jarvis-validation-v1:$OwnershipDigest") { throw 'owner mismatch' }
    if ($Operation -in @('attach_media','detach_media','inspect_media')) {
        Assert-MediaVmSafety $nativeVm
        if (-not $MediaPath -or [IO.Path]::GetFileName($MediaPath) -cne 'source.iso' -or
            -not [IO.Path]::IsPathRooted($MediaPath) -or $MediaPath.StartsWith('\\') -or
            $MediaBytes -le 0 -or $MediaBytes -gt 140000000 -or -not $MediaDigest) { throw 'invalid media binding' }
        $selectedPath = [IO.Path]::GetFullPath($MediaPath)
        $drives = @(Get-VMDvdDrive -VM $nativeVm -ErrorAction Stop)
        if ($drives.Count -gt 1) { throw 'ambiguous media drives' }
        if ($drives.Count -eq 1 -and $drives[0].Path -and
            -not [string]::Equals([IO.Path]::GetFullPath($drives[0].Path), $selectedPath, [StringComparison]::OrdinalIgnoreCase)) { throw 'foreign media' }
        if ($Operation -eq 'attach_media' -or ($Operation -eq 'inspect_media' -and $drives.Count -eq 1 -and $drives[0].Path)) {
            $item = Get-Item -LiteralPath $selectedPath -ErrorAction Stop
            if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'unsafe media' }
            $mediaStream = [IO.File]::Open($selectedPath, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
            try {
                if ($mediaStream.Length -ne $MediaBytes) { throw 'media length mismatch' }
                $sha = [Security.Cryptography.SHA256]::Create()
                try { $actualDigest = ([BitConverter]::ToString($sha.ComputeHash($mediaStream))).Replace('-','').ToLowerInvariant() }
                finally { $sha.Dispose() }
                if ($actualDigest -cne $MediaDigest) { throw 'media hash mismatch' }
                if ($Operation -eq 'attach_media') {
                    if ($drives.Count -eq 0) { Add-VMDvdDrive -VM $nativeVm -Path $selectedPath -ErrorAction Stop | Out-Null }
                    else { Set-VMDvdDrive -VMDvdDrive $drives[0] -Path $selectedPath -ErrorAction Stop }
                }
                $drives = @(Get-VMDvdDrive -VM $nativeVm -ErrorAction Stop)
                if ($drives.Count -ne 1 -or -not [string]::Equals([IO.Path]::GetFullPath($drives[0].Path), $selectedPath, [StringComparison]::OrdinalIgnoreCase)) { throw 'attachment uncertain' }
            } finally { $mediaStream.Dispose() }
        } elseif ($Operation -eq 'detach_media' -and $drives.Count -eq 1 -and $drives[0].Path) {
            Set-VMDvdDrive -VMDvdDrive $drives[0] -Path $null -ErrorAction Stop
            if (@(Get-VMDvdDrive -VM $nativeVm | Where-Object { $_.Path }).Count) { throw 'detachment uncertain' }
        }
        $finalDrives = @(Get-VMDvdDrive -VM $nativeVm -ErrorAction Stop)
        if ($finalDrives.Count -gt 1 -or ($finalDrives.Count -eq 1 -and $finalDrives[0].Path -and
            -not [string]::Equals([IO.Path]::GetFullPath($finalDrives[0].Path), $selectedPath, [StringComparison]::OrdinalIgnoreCase))) { throw 'media changed during observation' }
        $nativeVm = Get-VM -Id $VmId -ErrorAction Stop
        Assert-MediaVmSafety $nativeVm
        $mediaState = if (@($finalDrives | Where-Object { $_.Path }).Count -eq 1) { 'attached' } else { 'detached' }
        @{ vmId = [string]$VmId; ownershipDigest = $OwnershipDigest; hostMachineGuid = [string]$actualHostGuid;
           mediaDigest = $MediaDigest; mediaBytes = $MediaBytes; mediaState = $mediaState;
           commandOutcome = 'unmeasured'; executionAuthorized = $false } | ConvertTo-Json -Compress
        exit 0
    }
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
