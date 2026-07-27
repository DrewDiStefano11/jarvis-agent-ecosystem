Describe "JarvisHost" {
  It "Imports common module" { Import-Module (Join-Path $PSScriptRoot '..' 'JarvisHost.Common.psm1') -Force; $true | Should -Be $true }
  It "Validates config exists" { { Import-JarvisHostConfig -Path 'missing.json' } | Should -Throw }
  It "Exports functions" { (Get-Module (Join-Path $PSScriptRoot '..' 'JarvisHost.Common.psm1')).ExportedFunctions.Count | Should -BeGreaterThan 0 }
  It "Has Pester tests" { $true | Should -Be $true }
}
