# Complete rewritten common module - single definitions, PowerShell 5.1 compatible
function Import-JarvisHostConfig {
  param([string]$Path)
  if (-not (Test-Path $Path)) { throw "Config missing: $Path" }
  $raw = Get-Content $Path -Raw
  $jsonText = $raw -replace '\r\n', '\n' -replace '\r', '\n'
  $json = $jsonText | ConvertFrom-Json
  $required = @('repoPath','backendPort','frontendPort','databasePath','logDirectory','stateDirectory')
  $keys = @()
  if ($json -is [System.Collections.IDictionary]) { $keys = $json.Keys } else { $keys = $json.PSObject.Properties.Name }
  foreach ($r in $required) {
    $has = $false
    foreach ($k in $keys) { if ([string]$k -eq $r) { $has = $true; break } }
    if (-not $has) { throw "Missing required config field: $r" }
  }
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
# Named mutex for cross-process exclusive locking
function Get-LockName {
  param([string]$Purpose)
  $canonical = $Purpose.ToLower() -replace '[^a-z0-9]', '_'
  return "Global\JarvisHostLock_" + $canonical
}
function New-JarvisHostLock {
  param([string]$LockPurpose)
  $mutexName = Get-LockName -Purpose $LockPurpose
  $mutex = $null
  try {
    $mutex = [System.Threading.Mutex]::new($false, $mutexName)
  } catch {
    $mutex = $null
  }
  if (-not $mutex) {
    try {
      $mutex = [System.Threading.Mutex]::OpenExisting($mutexName)
    } catch {
      throw "Failed to create or open mutex: $mutexName"
    }
  }
  $acquired = [System.Threading.Mutex]::WaitOne($mutex, [System.TimeSpan]::FromSeconds(30))
  if (-not $acquired) {
    $mutex.Dispose()
    throw "Lock contention: could not acquire $mutexName within timeout"
  }
  $metadata = @{ ownerPID = [System.Diagnostics.Process]::GetCurrentProcess().Id; acquired = (Get-Date -Format 'o'); instanceId = [System.Guid]::NewGuid().ToString() }
  return @{ mutex = $mutex; purpose = $LockPurpose; metadata = $metadata }
}
function Remove-JarvisHostLock {
  param([hashtable]$LockHandle)
  if ($LockHandle -and $LockHandle.mutex) {
    try { $LockHandle.mutex.ReleaseMutex() } catch {}
    try { $LockHandle.mutex.Dispose() } catch {}
  }
}
function Read-JarvisProcessMetadata {
  param([string]$Path)
  if (Test-Path $Path) {
    $raw = Get-Content $Path -Raw
    return $raw | ConvertFrom-Json
  }
  return @{ }
}
function Write-JarvisProcessMetadata {
  param([string]$Path, [hashtable]$Data)
  $dir = Split-Path $Path
  if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
  $tmp = $Path + ".tmp." + ([System.Guid]::NewGuid().ToString())
  $Data | ConvertTo-Json -Depth 10 | Set-Content -Path $tmp -NoNewline
  Move-Item -Path $tmp -Destination $Path -Force
}
function Get-ProcessCreationTime {
  param([int]$PID)
  try {
    $wmi = Get-WmiObject Win32_Process -Filter "ProcessId=$PID" -ErrorAction Stop
    return [System.Management.ManagementDateTimeConverter]::ToDateTime($wmi.CreationDate)
  } catch { return $null }
}
function Get-ProcessExecutablePath {
  param([int]$PID)
  try {
    $wmi = Get-WmiObject Win32_Process -Filter "ProcessId=$PID" -ErrorAction Stop
    return $wmi.ExecutablePath
  } catch { return $null }
}
function Get-ProcessWorkingDirectory {
  param([int]$PID)
  try {
    $proc = Get-Process -Id $PID -ErrorAction Stop
    return $proc.StartInfo.WorkingDirectory
  } catch { return $null }
}
function Get-ProcessArguments {
  param([int]$PID)
  try {
    $wmi = Get-WmiObject Win32_Process -Filter "ProcessId=$PID" -ErrorAction Stop
    return $wmi.CommandLine
  } catch { return $null }
}
function Test-JarvisProcessOwnership {
  param([int]$PID, [string]$ExpectedExecutable, [string]$ExpectedWorkingDir, [string]$ExpectedArguments, [datetime]$ExpectedStartTime, [string]$ExpectedInstanceId)
  try {
    $procWmi = Get-WmiObject Win32_Process -Filter "ProcessId=$PID" -ErrorAction Stop
    $procPath = $procWmi.ExecutablePath
    $creationTime = [System.Management.ManagementDateTimeConverter]::ToDateTime($procWmi.CreationDate)
    $pathMatch = ([string]$procPath -eq $ExpectedExecutable)
    $startMatch = $true
    if ($ExpectedStartTime) {
      $diff = [Math]::Abs(($creationTime - $ExpectedStartTime).TotalSeconds)
      $startMatch = ($diff -le 5)
    }
    $args = $procWmi.CommandLine
    $argsMatch = $true
    if ($ExpectedArguments) {
      $argsMatch = ($args -like ("*" + $ExpectedArguments + "*"))
    }
    $cwd = Get-ProcessWorkingDirectory -PID $PID
    $cwdMatch = ([string]$cwd -eq $ExpectedWorkingDir)
    return ($pathMatch -and $startMatch -and $argsMatch -and $cwdMatch)
  } catch { return $false }
}
function Get-JarvisPortOwner {
  param([int]$Port)
  try {
    $net = netstat -ano 2>$null | Select-String (":" + $Port + ".*")
    if ($net) {
      $line = $net.Line
      $lineStr = $line.ToString()
      if ($lineStr -match '(\d+)\s*$') {
        $pidStr = $Matches[1].Trim()
        $pidNum = [int]::Parse($pidStr)
        $procPath = Get-ProcessExecutablePath -PID $pidNum
        $procName = (Get-Process -Id $pidNum -ErrorAction SilentlyContinue).ProcessName
        return @{ pid = $pidNum; executable = $procPath; name = $procName; port = $Port; address = '127.0.0.1' }
      }
    }
  } catch {}
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
  $procWmi = Get-WmiObject Win32_Process -Filter "ProcessId=$($procInfo.Id)" -ErrorAction SilentlyContinue
  return @{ pid = $procInfo.Id; executable = $FilePath; arguments = $ArgumentList; workingDirectory = $WorkingDirectory; processStartTime = $procWmi.CreationDate; instanceId = [System.Guid]::NewGuid().ToString(); timestamp = (Get-Date -Format 'o') }
}
function Stop-JarvisOwnedProcess {
  param([int]$PID, [string]$ExpectedExecutable, [string]$ExpectedWorkingDir, [string]$ExpectedArguments, [datetime]$ExpectedStartTime, [string]$ExpectedInstanceId, [int]$GracefulTimeoutSec, [switch]$Force)
  $verified = Test-JarvisProcessOwnership -PID $PID -ExpectedExecutable $ExpectedExecutable -ExpectedWorkingDir $ExpectedWorkingDir -ExpectedArguments $ExpectedArguments -ExpectedStartTime $ExpectedStartTime -ExpectedInstanceId $ExpectedInstanceId
  if (-not $verified) { return @{ stopped = $false; verified = $false; reason = "ownership_failed"; pid = $PID } }
  try {
    Stop-Process -Id $PID -Force:$Force -ErrorAction Stop
    if (-not $Force) { Start-Sleep -Seconds ([Math]::Min($GracefulTimeoutSec, 5)) }
    $alive = Get-Process -Id $PID -ErrorAction SilentlyContinue
    return @{ stopped = (-not $alive); verified = $verified; pid = $PID }
  } catch {
    return @{ stopped = $false; verified = $verified; error = $_.Exception.Message; pid = $PID }
  }
}
function Get-JarvisRestartHistory {
  param([string]$HistoryPath)
  $dir = Split-Path $HistoryPath
  if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
  if (Test-Path $HistoryPath) {
    $raw = Get-Content $HistoryPath -Raw
    $items = @()
    $lines = $raw -split "`n"
    foreach ($line in $lines) {
      $line = $line.Trim()
      if ($line) {
        $item = $line | ConvertFrom-Json -AsHashtable -ErrorAction SilentlyContinue
        if ($item) { $items += $item }
      }
    }
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
  $tmp = $HistoryPath + ".tmp." + [System.Guid]::NewGuid().ToString()
  $hist | ConvertTo-Json -Depth 10 | Set-Content -Path $tmp -NoNewline
  Move-Item -Path $tmp -Destination $HistoryPath -Force
}
function Test-JarvisCrashLoop {
  param([string]$HistoryPath, [int]$WindowMinutes, [int]$MaxRestarts)
  $hist = Get-JarvisRestartHistory -HistoryPath $HistoryPath
  $now = Get-Date
  $count = 0
  foreach ($record in $hist) {
    if ($record.timestamp) {
      try { $ts = [datetime]::Parse($record.timestamp); $diff = ($now - $ts).TotalMinutes; if ($diff -le $WindowMinutes) { $count = $count + 1 } } catch {}
    }
  }
  return ($count -ge $MaxRestarts)
}
function Set-JarvisCrashLoopLatch {
  param([string]$LatchPath)
  $dir = Split-Path $LatchPath
  if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
  @{ latched = $true; time = (Get-Date -Format 'o') } | ConvertTo-Json -Depth 5 | Set-Content -Path $LatchPath -Force
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
  return $null
}
function Invoke-JarvisTailscale {
  param([string[]]$Arguments)
  $exe = Get-JarvisTailscaleExecutable
  if (-not $exe) { throw "Tailscale CLI not found" }
  $proc = Start-Process -FilePath $exe -ArgumentList $Arguments -PassThru -Wait -WindowStyle Hidden
  return @{ exitCode = $proc.ExitCode; executable = $exe; arguments = $Arguments }
}
function Test-JarvisSQLiteIntegrity {
  param([string]$DatabasePath)
  $tmpScript = [System.IO.Path]::GetTempFileName() + ".py"
  $dbPathQuoted = $DatabasePath -replace '"', '\"'
  $pyContent = "import sqlite3; c=sqlite3.connect('$dbPathQuoted'); r=c.execute('PRAGMA integrity_check').fetchone(); c.close(); exit(0 if r and r[0]=='ok' else 1)"
  $pyContent | Set-Content -Path $tmpScript -Force
  $proc = Start-Process -FilePath "python" -ArgumentList $tmpScript -PassThru -Wait -WindowStyle Hidden -RedirectStandardOutput (Join-Path $env:TEMP ("sqlite_stdout_" + [System.Guid]::NewGuid().ToString() + ".txt")) -RedirectStandardError (Join-Path $env:TEMP ("sqlite_stderr_" + [System.Guid]::NewGuid().ToString() + ".txt"))
  $exitCode = $proc.ExitCode
  Remove-Item $tmpScript -Force -ErrorAction SilentlyContinue
  return ($exitCode -eq 0)
}
function Get-JarvisToolkitManifest {
  param([string]$ManifestPath)
  if (Test-Path $ManifestPath) {
    return Get-Content $ManifestPath -Raw | ConvertFrom-Json -AsHashtable
  }
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
    $lower = ($k.ToString()).ToLower()
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
  if (Test-Path $StatePath) {
    return Get-Content $StatePath -Raw | ConvertFrom-Json -AsHashtable
  }
  return @{}
}
function Set-JarvisHostState {
  param([string]$StatePath, [hashtable]$Data)
  $dir = Split-Path $StatePath
  if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
  $tmp = $StatePath + ".tmp." + ([System.Guid]::NewGuid().ToString())
  $Data | ConvertTo-Json -Depth 10 | Set-Content -Path $tmp -NoNewline
  Move-Item -Path $tmp -Destination $StatePath -Force
}
Export-ModuleMember -Function Import-JarvisHostConfig, Resolve-JarvisHostPath, Write-JarvisHostLog, New-JarvisHostLock, Remove-JarvisHostLock, Read-JarvisProcessMetadata, Write-JarvisProcessMetadata, Get-ProcessCreationTime, Get-ProcessExecutablePath, Get-ProcessWorkingDirectory, Get-ProcessArguments, Test-JarvisProcessOwnership, Get-JarvisPortOwner, Test-JarvisHealthEndpoint, Wait-JarvisHealthEndpoint, Start-JarvisOwnedProcess, Stop-JarvisOwnedProcess, Get-JarvisRestartHistory, Add-JarvisRestartRecord, Test-JarvisCrashLoop, Set-JarvisCrashLoopLatch, Clear-JarvisCrashLoopLatch, Get-JarvisCrashLoopLatch, Get-JarvisTailscaleExecutable, Invoke-JarvisTailscale, Test-JarvisSQLiteIntegrity, Get-JarvisToolkitManifest, Write-JarvisToolkitManifest, Redact-JarvisConfig, Get-JarvisHostState, Set-JarvisHostState
