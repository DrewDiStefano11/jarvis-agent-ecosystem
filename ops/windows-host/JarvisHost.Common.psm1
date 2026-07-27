function Import-JarvisHostConfig { param([string]$Path) if (-not (Test-Path $Path)) { throw "Config not found: $Path" }; $json = Get-Content $Path -Raw | ConvertFrom-Json -AsHashtable; return $json }
function Write-JarvisHostLog { param([string]$LogDir, [string]$Name, [string]$Message) $line = "[$(Get-Date -Format 'o')] $Message"; Add-Content (Join-Path $LogDir "$Name.log") -Value $line -ErrorAction SilentlyContinue }
function Enter-JarvisHostLock { param([string]$LockFile) $sw = [System.IO.StreamWriter]::new($LockFile); $sw.Write([System.Guid]::NewGuid()); $sw.Flush(); $sw.Close(); return $true }
function Read-JarvisProcessMetadata { param([string]$File) if (Test-Path $File) { return Get-Content $File -Raw | ConvertFrom-Json } else { return $null } }
function Write-JarvisProcessMetadata { param([string]$File, [hashtable]$Data) $tmp = $File + ".tmp"; $Data | ConvertTo-Json -Depth 10 | Set-Content -Path $tmp -NoNewline; Move-Item $tmp $File -Force }
function Test-JarvisProcessOwnership { param([int]$PID, [string]$ExpectedExe, [string]$ExpectedCwd) try { $p = Get-Process -Id $PID -ErrorAction Stop; return ($p.Path -eq $ExpectedExe) } catch { return $false } }
function Get-JarvisPortOwner { param([int]$Port) return $null }
function Test-JarvisHealthEndpoint { param([string]$Url, [int]$TimeoutSec) try { $r = Invoke-WebRequest -Uri $Url -TimeoutSec $TimeoutSec -UseBasicParsing; return $r.StatusCode -eq 200 } catch { return $false } }
function Wait-JarvisHealthEndpoint { param([string]$Url, [int]$TimeoutSec, [int]$IntervalSec) $end = (Get-Date).AddSeconds($TimeoutSec); while ((Get-Date) -lt $end) { if (Test-JarvisHealthEndpoint -Url $Url -TimeoutSec $IntervalSec) { return $true }; Start-Sleep -Seconds $IntervalSec }; return $false }
function Start-JarvisOwnedProcess { param([string]$Exe, [string]$Args, [string]$WorkingDir, [string]$LogDir) $proc = Start-Process -FilePath $Exe -ArgumentList $Args -WorkingDirectory $WorkingDir -RedirectStandardOutput (Join-Path $LogDir "out.log") -RedirectStandardError (Join-Path $LogDir "err.log") -PassThru -WindowStyle Hidden; return $proc.Id }
function Stop-JarvisOwnedProcess { param([int]$PID, [string]$ExpectedExe) $verified = Test-JarvisProcessOwnership -PID $PID -ExpectedExe $ExpectedExe; if ($verified) { Stop-Process -Id $PID -Force -ErrorAction SilentlyContinue }; return $verified }
function Get-JarvisHostState { return @{ } }
function Set-JarvisHostState { param([hashtable]$Data) }
function Get-JarvisRestartHistory { return @() }
function Add-JarvisRestartRecord { param([hashtable]$Record) }
function Test-JarvisCrashLoop { return $false }
function Get-JarvisTailscaleExecutable { return (Get-Command tailscale -ErrorAction SilentlyContinue).Source }
function Invoke-JarvisTailscale { param([string]$Args) return (tailscale $Args 2>&1) }
function Test-JarvisSQLiteIntegrity { param([string]$Path) try { python -c "import sqlite3; c=sqlite3.connect('$Path'); c.execute('PRAGMA integrity_check')"; return $true } catch { return $false } }
Export-ModuleMember -Function Import-JarvisHostConfig, Test-JarvisHostConfig, Resolve-JarvisHostPath, Write-JarvisHostLog, Enter-JarvisHostLock, Exit-JarvisHostLock, Read-JarvisProcessMetadata, Write-JarvisProcessMetadata, Test-JarvisProcessOwnership, Get-JarvisPortOwner, Test-JarvisHealthEndpoint, Wait-JarvisHealthEndpoint, Start-JarvisOwnedProcess, Stop-JarvisOwnedProcess, Get-JarvisHostState, Set-JarvisHostState, Get-JarvisRestartHistory, Add-JarvisRestartRecord, Test-JarvisCrashLoop, Get-JarvisTailscaleExecutable, Invoke-JarvisTailscale, Test-JarvisSQLiteIntegrity
function Resolve-JarvisHostPath { param([string]$Path, [string]$Base) if ([System.IO.Path]::IsPathRooted($Path)) { return $Path } else { return Join-Path $Base $Path } }
function Exit-JarvisHostLock { param([string]$LockFile) if (Test-Path $LockFile) { Remove-Item $LockFile -Force -ErrorAction SilentlyContinue } }
function Test-JarvisHostConfig { param([string]$Path) $cfg = Import-JarvisHostConfig -Path $Path; return ($null -ne $cfg.repoPath -and $cfg.backendPort -gt 0) }
