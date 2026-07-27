[CmdletBinding(SupportsShouldProcess)]
param([string]$ConfigPath = (Join-Path $PSScriptRoot 'jarvis-host.json'), [switch]$Force, [int]$GracefulTimeoutSec = 10)
Import-Module (Join-Path $PSScriptRoot 'JarvisHost.Common.psm1') -Force
$cfg = Import-JarvisHostConfig -Path $ConfigPath
if ($PSCmdlet.ShouldProcess("Host", "Stop services")) {
  $files = @('frontend.json', 'backend.json')
  foreach ($file in $files) {
    $metaPath = Join-Path $cfg.stateDirectory $file
    $meta = Read-JarvisProcessMetadata -Path $metaPath
    if ($meta.pid -and [int]$meta.pid) {
      $verified = Test-JarvisProcessOwnership -PID ([int]$meta.pid) -ExpectedExecutable $meta.executable -ExpectedWorkingDir $meta.workingDirectory -ExpectedArguments $meta.arguments -ExpectedStartTime ([datetime]::Parse($meta.processStartTime))
      if ($verified) {
        $result = Stop-JarvisOwnedProcess -PID ([int]$meta.pid) -ExpectedExecutable $meta.executable -ExpectedWorkingDir $meta.workingDirectory -ExpectedArguments $meta.arguments -ExpectedStartTime ([datetime]::Parse($meta.processStartTime)) -GracefulTimeoutSec $GracefulTimeoutSec -Force:$Force
        Write-Output "Stopped $file: verified=$($result.verified) stopped=$($result.stopped) pid=$($meta.pid)"
      } else {
        Write-Output "Metadata for $file exists but process $($meta.pid) is unverified or stale; not terminating."
      }
    }
    if (Test-Path $metaPath) {
      @{ pid = $null; cleared = (Get-Date -Format 'o') } | ConvertTo-Json | Set-Content -Path ($metaPath + ".clear") -NoNewline
      Move-Item -Path ($metaPath + ".clear") -Destination $metaPath -Force
    }
  }
}
