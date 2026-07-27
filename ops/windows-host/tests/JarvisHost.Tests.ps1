Describe "JarvisHost Common" {
  BeforeAll { Import-Module (Join-Path $PSScriptRoot '..' 'JarvisHost.Common.psm1') -Force }
  It "Exports Resolve-JarvisHostPath" { (Get-Module (Join-Path $PSScriptRoot '..' 'JarvisHost.Common.psm1')).ExportedFunctions.ContainsKey('Resolve-JarvisHostPath') | Should -Be $true }
  It "Exports Import-JarvisHostConfig" { (Get-Module (Join-Path $PSScriptRoot '..' 'JarvisHost.Common.psm1')).ExportedFunctions.ContainsKey('Import-JarvisHostConfig') | Should -Be $true }
  It "Exports Write-JarvisProcessMetadata" { (Get-Module (Join-Path $PSScriptRoot '..' 'JarvisHost.Common.psm1')).ExportedFunctions.ContainsKey('Write-JarvisProcessMetadata') | Should -Be $true }
  It "Exports Test-JarvisProcessOwnership" { (Get-Module (Join-Path $PSScriptRoot '..' 'JarvisHost.Common.psm1')).ExportedFunctions.ContainsKey('Test-JarvisProcessOwnership') | Should -Be $true }
  It "Exports Test-JarvisCrashLoop" { (Get-Module (Join-Path $PSScriptRoot '..' 'JarvisHost.Common.psm1')).ExportedFunctions.ContainsKey('Test-JarvisCrashLoop') | Should -Be $true }
  It "Exports Redact-JarvisConfig" { (Get-Module (Join-Path $PSScriptRoot '..' 'JarvisHost.Common.psm1')).ExportedFunctions.ContainsKey('Redact-JarvisConfig') | Should -Be $true }
  It "Exports Get-JarvisPortOwner" { (Get-Module (Join-Path $PSScriptRoot '..' 'JarvisHost.Common.psm1')).ExportedFunctions.ContainsKey('Get-JarvisPortOwner') | Should -Be $true }
  It "Exports Get-JarvisToolkitManifest" { (Get-Module (Join-Path $PSScriptRoot '..' 'JarvisHost.Common.psm1')).ExportedFunctions.ContainsKey('Get-JarvisToolkitManifest') | Should -Be $true }
}
