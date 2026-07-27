# Private Remote Access
## Architecture
Tailnet-only; backend/frontend on localhost; Tailscale Serve proxies.
## Prerequisites
Tailscale installed and authenticated.
## Serve Configuration
Configure-JarvisTailscale.ps1 creates routes; Remove-JarvisTailscale.ps1 removes toolkit routes only.
## Verification
Access from authorized tailnet device; test WebSocket upgrade.
## Security
No Funnel; no router forwarding; loopback binding preserved; ACL recommendations included.
## Security Constraints
- Tailscale Serve only; no Funnel; loopback preserved; no public firewall rules.
