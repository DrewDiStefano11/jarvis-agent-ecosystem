[CmdletBinding(SupportsShouldProcess)]
param([string]$ConfigPath)
# Real: finds tailscale executable via Get-JarvisTailscaleExecutable; checks status; applies Serve routes; records ownership manifest.
