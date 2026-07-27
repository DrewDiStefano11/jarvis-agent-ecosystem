# Complete rewritten common module - single definitions, real logic
function Import-JarvisHostConfig {
  param([string]$Path)
  if (-not (Test-Path $Path)) { throw "Config missing: $Path" }
  $json = Get-Content $Path -Raw | ConvertFrom-Json -AsHashtable
  $required = @('repoPath','backendPort','frontendPort','databasePath','logDirectory','stateDirectory')
  foreach ($r in $required) { if (-not $json.ContainsKey($r)) { throw "Missing required config field: $r" } }
  return $json
}
function Resolve-JarvisHostPath {
  param([string]$Path, [string]$BaseDir)
  if ([System.IO.Path]::IsPathRooted($Path)) { return $Path }
  else { return Join-Path $BaseDir $Path }
}
function Write-JarvisHostLog {
  param([string]$LogDir, [string]$FileName, [string]$Message)
  $line = "[$(Get-Date -Format 'o')] $Message"
  $target = Join-Path $LogDir $FileName
  $dir = Split-Path $target
  if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
  Add-Content -Path $target -Value $line -ErrorAction SilentlyContinue
}
function New-JarvisHostLock {
  param([string]$LockPath)
  $metadata = @{ owner = $PID; processStartTime = (Get-Process -Id $PID -ErrorAction SilentlyContinue).StartTime; instanceId = [System.Guid]::NewGuid(); acquired = (Get-Date -Format 'o') }
  $dir = Split-Path $LockPath
  if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
  $stream = [System.IO.FileStream]::new($LockPath, [System.IO.FileMode]::Create, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
  $writer = [System.IO.StreamWriter]::new($stream)
  $writer.Write(($metadata | ConvertTo-Json -Depth 5))
  $writer.Flush()
  $writer.Close()
  return @{ stream = $stream; lockFile = $LockPath; metadata = $metadata }
}
function Remove-JarvisHostLock {
  param([hashtable]$LockHandle)
  if ($LockHandle -and $LockHandle.stream) { try { $LockHandle.stream.Close(); $LockHandle.stream.Dispose() } catch {} }
  if ($LockHandle -and $LockHandle.lockFile -and (Test-Path $LockHandle.lockFile)) { Remove-Item $LockHandle.lockFile -Force -ErrorAction SilentlyContinue }
}
function Read-JarvisProcessMetadata {
  param([string]$Path)
  if (Test-Path $Path) { return Get-Content $Path -Raw | ConvertFrom-Json -AsHashtable } else { return @{} }
}
function Write-JarvisProcessMetadata {
  param([string]$Path, [hashtable]$Data)
  $dir = Split-Path $Path
  if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
  $tmp = $Path + ".tmp." + [System.Guid]::NewGuid()
  $Data | ConvertTo-Json -Depth 10 | Set-Content -Path $tmp -NoNewline
  Move-Item -Path $tmp -Destination $Path -Force
}
function Get-ProcessCreationTime {
  param([int]$PID)
  try { return (Get-Process -Id $PID -ErrorAction Stop).StartTime } catch { return $null }
}
function Get-ProcessExecutablePath {
  param([int]$PID)
  try { return (Get-Process -Id $PID -ErrorAction Stop).Path } catch { return $null }
}
function Get-ProcessWorkingDirectory {
  param([int]$PID)
  try { return (Get-Process -Id $PID -ErrorAction Stop).StartInfo.WorkingDirectory } catch { return $null }
}
function Get-ProcessArguments {
  param([int]$PID)
  try { return (Get-WmiObject Win32_Process -Filter "ProcessId=$PID").CommandLine } catch { return $null }
}
function Test-JarvisProcessOwnership {
  param([int]$PID, [string]$ExpectedExecutable, [string]$ExpectedWorkingDir, [string]$ExpectedArguments, [datetime]$ExpectedStartTime)
  try {
    $proc = Get-Process -Id $PID -ErrorAction Stop
    if (-not $proc) { return $false }
    $pathMatch = ($proc.Path -eq $ExpectedExecutable)
    $startMatch = ($ExpectedStartTime -and $proc.StartTime -eq $ExpectedStartTime)
    $cwdMatch = ($proc.StartInfo.WorkingDirectory -eq $ExpectedWorkingDir)
    $argMatch = $true
    if ($ExpectedArguments) {
      $actualArgs = Get-ProcessArguments -PID $PID
      $argMatch = ($actualArgs -match [regex]::Escape($ExpectedArguments))
    }
    return ($pathMatch -and ($startMatch -or (-not $ExpectedStartTime)) -and $cwdMatch -and $argMatch)
  } catch { return $false }
}
function Get-JarvisPortOwner {
  param([int]$Port)
  $output = netstat -ano 2>$null | Select-String ":$Port" || true
  if ($output) {
    $line = $output -split "`n" | Where-Object { $_ -match ":$Port" } | Select-Object -First 1
    if ($line -match "(\d+)\s*$") {
      $pidStr = $Matches[1]
      try {
        $proc = Get-Process -Id ([int]$pidStr) -ErrorAction Stop
        return @{ pid = [int]$pidStr; executable = $proc.Path; name = $proc.ProcessName }
      } catch { return @{ pid = [int]$pidStr; executable = $null; name = $null } }
    }
  }
  return $null
}
function Test-JarvisHealthEndpoint {
  param([string]$Url, [int]$TimeoutSec)
  try {
    $r = Invoke-WebRequest -Uri $Url -TimeoutSec $TimeoutSec -UseBasicParsing -ErrorAction Stop
    return ($r.StatusCode -eq 200)
  } catch { return $false }
}
function Wait-JarvisHealthEndpoint {
  param([string]$Url, [int]$TotalTimeoutSec, [int]$IntervalSec)
  $end = (Get-Date).AddSeconds($TotalTimeoutSec)
  while ((Get-Date) -lt $end) {
    if (Test-JarvisHealthEndpoint -Url $Url -TimeoutSec $IntervalSec) { return $true }
    else { Start-Sleep -Seconds $IntervalSec }
  }
  return $false
}
function Start-JarvisOwnedProcess {
  param([string]$FilePath, [string]$ArgumentList, [string]$WorkingDirectory, [string]$LogDir)
  $dir = Split-Path $LogDir
  if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
  $procInfo = Start-Process -FilePath $FilePath -ArgumentList $ArgumentList -WorkingDirectory $WorkingDirectory -RedirectStandardOutput (Join-Path $LogDir "out.log") -RedirectStandardError (Join-Path $LogDir "err.log") -PassThru -WindowStyle Hidden
  $proc = Get-Process -Id $procInfo.Id -ErrorAction SilentlyContinue
  return @{ pid = $procInfo.Id; executable = $FilePath; arguments = $ArgumentList; workingDirectory = $WorkingDirectory; processStartTime = $proc.StartTime; instanceId = [System.Guid]::NewGuid(); timestamp = (Get-Date -Format 'o') }
}
function Stop-JarvisOwnedProcess {
  param([int]$PID, [string]$ExpectedExecutable, [string]$ExpectedWorkingDir, [string]$ExpectedArguments, [datetime]$ExpectedStartTime, [int]$GracefulTimeoutSec, [switch]$Force)
  $verified = Test-JarvisProcessOwnership -PID $PID -ExpectedExecutable $ExpectedExecutable -ExpectedWorkingDir $ExpectedWorkingDir -ExpectedArguments $ExpectedArguments -ExpectedStartTime $ExpectedStartTime
  if (-not $verified) { return @{ stopped = $false; verified = $false; reason = "ownership_verification_failed" } }
  try {
    Stop-Process -Id $PID -Force:$Force -ErrorAction Stop
    if (-not $Force) { Start-Sleep -Seconds ([Math]::Min($GracefulTimeoutSec, 5)) }
    $alive = Get-Process -Id $PID -ErrorAction SilentlyContinue
    return @{ stopped = (-not $alive); verified = $verified; pid = $PID }
  } catch {
    return @{ stopped = $false; verified = $verified; error = $_.Exception.Message }
  }
}
function Get-JarvisRestartHistory {
  param([string]$HistoryPath)
  $dir = Split-Path $HistoryPath
  if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
  if (Test-Path $HistoryPath) {
    $raw = Get-Content $HistoryPath -Raw
    $items = @()
    foreach ($line in ($raw -split "`n")) { $line = $line.Trim(); if ($line) { $items += ($line | ConvertFrom-Json -AsHashtable -ErrorAction SilentlyContinue) } }
    return $items
  }
  return @()
}
function Add-JarvisRestartRecord {
  param([string]$HistoryPath, [hashtable]$Record)
  $hist = Get-JarvisRestartHistory -HistoryPath $HistoryPath
  $hist += $Record
  $dir = Split-Path $HistoryPath
  if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
  $tmp = $HistoryPath + ".tmp." + [System.Guid]::NewGuid()
  $hist | ConvertTo-Json -Depth 10 | Set-Content -Path $tmp -NoNewline
  Move-Item -Path $tmp -Destination $HistoryPath -Force
}
function Test-JarvisCrashLoop {
  param([string]$HistoryPath, [int]$WindowMinutes, [int]$MaxRestarts)
  $hist = Get-JarvisRestartHistory -HistoryPath $HistoryPath
  $now = Get-Date
  $count = 0
  for ($i = 0; $i -lt $hist.Count; $i++) {
    if ($hist[$i].timestamp) {
      $ts = [datetime]::Parse($hist[$i].timestamp)
      $diff = ($now - $ts).TotalMinutes
      if ($diff -le $WindowMinutes) { $count++ }
    }
  }
  return ($count -ge $MaxRestarts)
}
function Set-JarvisCrashLoopLatch {
  param([string]$LatchPath)
  $dir = Split-Path $LatchPath
  if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
  @{ latched = $true; time = (Get-Date -Format 'o'); instanceId = [System.Guid]::NewGuid() } | ConvertTo-Json -Depth 5 | Set-Content -Path $LatchPath -Force
}
function Clear-JarvisCrashLoopLatch {
  param([string]$LatchPath)
  if (Test-Path $LatchPath) { Remove-Item $LatchPath -Force -ErrorAction SilentlyContinue }
}
function Get-JarvisCrashLoopLatch {
  param([string]$LatchPath)
  if (Test-Path $LatchPath) {
    $data = Get-Content $LatchPath -Raw | ConvertFrom-Json -AsHashtable
    return ($data.latched -eq $true)
  }
  return $false
}
function Get-JarvisTailscaleExecutable {
  $cmd = Get-Command tailscale -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  else { return $null }
}
function Invoke-JarvisTailscale {
  param([string[]]$Arguments)
  $exe = Get-JarvisTailscaleExecutable
  if (-not $exe) { throw "Tailscale CLI not found" }
  $proc = Start-Process -FilePath $exe -ArgumentList $Arguments -PassThru -Wait -WindowStyle Hidden
  return $proc.ExitCode
}
function Test-JarvisSQLiteIntegrity {
  param([string]$DatabasePath)
  $tmpScript = [System.IO.Path]::GetTempFileName() + ".py"
  $pyContent = "import sqlite3; c=sqlite3.connect('$DatabasePath'); r=c.execute('PRAGMA integrity_check').fetchone(); c.close(); exit(0 if r and r[0]=='ok' else 1)"
  $pyContent | Set-Content -Path $tmpScript -Force
  $proc = Start-Process -FilePath "python" -ArgumentList $tmpScript -PassThru -Wait -WindowStyle Hidden -NoNewWindow
  $exitCode = $proc.ExitCode
  $stdout = $proc.StandardOutput
  $stderr = $proc.StandardError
  Remove-Item $tmpScript -Force -ErrorAction SilentlyContinue
  return ($exitCode -eq 0)
}
function Get-JarvisToolkitManifest {
  param([string]$ManifestPath)
  if (Test-Path $ManifestPath) { return Get-Content $ManifestPath -Raw | ConvertFrom-Json -AsHashtable }
  return @{}
}
function Write-JarvisToolkitManifest {
  param([string]$ManifestPath, [hashtable]$Data)
  $dir = Split-Path $ManifestPath
  if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
  $Data | ConvertTo-Json -Depth 10 | Set-Content -Path $ManifestPath -Force
}
function Redact-JarvisConfig {
  param([hashtable]$Config)
  $redacted = @{}
  foreach ($k in $Config.Keys) {
    $lower = $k.ToString().ToLower()
    if ($lower.Contains('secret') -or $lower.Contains('token') -or $lower.Contains('key') -or $lower.Contains('password') -or $lower.Contains('credential')) {
      $redacted[$k] = '[REDACTED]'
    } else {
      $redacted[$k] = $Config[$k]
    }
  }
  return $redacted
}
function Get-JarvisHostState {
  param([string]$StatePath)
  if (Test-Path $StatePath) { return Get-Content $StatePath -Raw | ConvertFrom-Json -AsHashtable }
  return @{}
}
function Set-JarvisHostState {
  param([string]$StatePath, [hashtable]$Data)
  $dir = Split-Path $StatePath
  if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
  $tmp = $StatePath + ".tmp." + [System.Guid]::NewGuid()
  $Data | ConvertTo-Json -Depth 10 | Set-Content -Path $tmp -NoNewline
  Move-Item -Path $tmp -Destination $StatePath -Force
}
Export-ModuleMember -Function Import-JarvisHostConfig, Resolve-JarvisHostPath, Write-JarvisHostLog, New-JarvisHostLock, Remove-JarvisHostLock, Read-JarvisProcessMetadata, Write-JarvisProcessMetadata, Get-ProcessCreationTime, Get-ProcessExecutablePath, Get-ProcessWorkingDirectory, Get-ProcessArguments, Test-JarvisProcessOwnership, Get-JarvisPortOwner, Test-JarvisHealthEndpoint, Wait-JarvisHealthEndpoint, Start-JarvisOwnedProcess, Stop-JarvisOwnedProcess, Get-JarvisRestartHistory, Add-JarvisRestartRecord, Test-JarvisCrashLoop, Set-JarvisCrashLoopLatch, Clear-JarvisCrashLoopLatch, Get-JarvisCrashLoopLatch, Get-JarvisTailscaleExecutable, Invoke-JarvisTailscale, Test-JarvisSQLiteIntegrity, Get-JarvisToolkitManifest, Write-JarvisToolkitManifest, Redact-JarvisConfig, Get-JarvisHostState, Set-JarvisHostState
