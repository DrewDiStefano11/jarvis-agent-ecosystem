Describe "JarvisHost Module" {
  BeforeAll { Import-Module (Join-Path $PSScriptRoot '..' 'JarvisHost.Common.psm1') -Force }
  It "Exports Import-JarvisHostConfig" { (Get-Module (Join-Path $PSScriptRoot '..' 'JarvisHost.Common.psm1')).ExportedFunctions.ContainsKey('Import-JarvisHostConfig') | Should -Be $true }
  It "Exports Resolve-JarvisHostPath" { (Get-Module (Join-Path $PSScriptRoot '..' 'JarvisHost.Common.psm1')).ExportedFunctions.ContainsKey('Resolve-JarvisHostPath') | Should -Be $true }
  It "Exports Write-JarvisHostLog" { (Get-Module (Join-Path $PSScriptRoot '..' 'JarvisHost.Common.psm1')).ExportedFunctions.ContainsKey('Write-JarvisHostLog') | Should -Be $true }
  It "Exports New-JarvisHostLock" { (Get-Module (Join-Path $PSScriptRoot '..' 'JarvisHost.Common.psm1')).ExportedFunctions.ContainsKey('New-JarvisHostLock') | Should -Be $true }
  It "Exports Remove-JarvisHostLock" { (Get-Module (Join-Path $PSScriptRoot '..' 'JarvisHost.Common.psm1')).ExportedFunctions.ContainsKey('Remove-JarvisHostLock') | Should -Be $true }
  It "Exports Read-JarvisProcessMetadata" { (Get-Module (Join-Path $PSScriptRoot '..' 'JarvisHost.Common.psm1')).ExportedFunctions.ContainsKey('Read-JarvisProcessMetadata') | Should -Be $true }
  It "Exports Write-JarvisProcessMetadata" { (Get-Module (Join-Path $PSScriptRoot '..' 'JarvisHost.Common.psm1')).ExportedFunctions.ContainsKey('Write-JarvisProcessMetadata') | Should -Be $true }
  It "Exports Test-JarvisProcessOwnership" { (Get-Module (Join-Path $PSScriptRoot '..' 'JarvisHost.Common.psm1')).ExportedFunctions.ContainsKey('Test-JarvisProcessOwnership') | Should -Be $true }
  It "Exports Get-JarvisPortOwner" { (Get-Module (Join-Path $PSScriptRoot '..' 'JarvisHost.Common.psm1')).ExportedFunctions.ContainsKey('Get-JarvisPortOwner') | Should -Be $true }
  It "Exports Test-JarvisCrashLoop" { (Get-Module (Join-Path $PSScriptRoot '..' 'JarvisHost.Common.psm1')).ExportedFunctions.ContainsKey('Test-JarvisCrashLoop') | Should -Be $true }
  It "Exports Get-JarvisToolkitManifest" { (Get-Module (Join-Path $PSScriptRoot '..' 'JarvisHost.Common.psm1')).ExportedFunctions.ContainsKey('Get-JarvisToolkitManifest') | Should -Be $true }
  It "Exports Redact-JarvisConfig" { (Get-Module (Join-Path $PSScriptRoot '..' 'JarvisHost.Common.psm1')).ExportedFunctions.ContainsKey('Redact-JarvisConfig') | Should -Be $true }
}
Describe "Config and Ownership Behavior" {
  It "Rejects missing config" {
    { Import-JarvisHostConfig -Path 'missing.json' } | Should -Throw
  }
  It "Rejects non-existent PID for ownership" {
    Test-JarvisProcessOwnership -PID 999999 -ExpectedExecutable 'fake' -ExpectedWorkingDir 'fake' | Should -Be $false
  }
  It "Creates safe JSON state" {
    $tmp = [System.IO.Path]::GetTempFileName() + '.json'
    @{ test = $true; pid = 123 } | ConvertTo-Json | Set-Content -Path $tmp -NoNewline
    $read = Get-Content $tmp -Raw | ConvertFrom-Json -AsHashtable
    $read.test | Should -Be $true
    Remove-Item $tmp -Force
  }
  It "Writes and reads toolkit manifest" {
    $tmp = [System.IO.Path]::GetTempFileName() + '.json'
    @{ routes = @('test'); target = 'localhost' } | ConvertTo-Json | Set-Content -Path $tmp -Force
    $m = Get-JarvisToolkitManifest -ManifestPath $tmp
    $m.routes[0] | Should -Be 'test'
    Remove-Item $tmp -Force
  }
}
