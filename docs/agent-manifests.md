# Agent manifests

Five permanent YAML manifests live in `agents/manifests`. Each `AgentManifest` uses `jarvis.local/v1alpha1` and includes identity/version/department/status plus parent, role, description, goals, capabilities, allowed and denied tools, memory scope, approval policy, execution limits/timeouts/retries, reviewer, deployment sandbox placeholder, Git requirement/branch convention, sprite, zone, and desk.

`apps/api/app/models/manifest.py` is the validation schema. Run `pytest tests/test_api.py -k manifests` from `apps/api` to load every manifest. Pydantic errors include the failing field path; missing required sections, empty goals/capabilities, or wrong field types fail validation.

Manifests describe simulated policy only. They do not deploy processes or grant tool access.
