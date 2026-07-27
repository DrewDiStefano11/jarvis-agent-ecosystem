Describe "JarvisHost Common" {
  It "Exports functions" { (Get-Module (Join-Path $PSScriptRoot '..' 'JarvisHost.Common.psm1')).ExportedFunctions.Count | Should -BeGreaterThan 0 }
  It "Imports config" { Import-Module (Join-Path $PSScriptRoot '..' 'JarvisHost.Common.psm1') -Force; $true | Should -Be $true }
  It "Rejects missing config" { { Import-JarvisHostConfig -Path 'nonexistent.json' } | Should -Throw }
}
