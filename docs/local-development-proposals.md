# Explicit local development proposals

This default-off path connects bounded source proposals to the existing native admission and exact source-approval workflow. It accepts only the configured native loopback Ollama adapter in `local_only` mode. Hosted OpenAI/Codex development paths remain quarantined. It does not execute generated code, run validation commands, publish changes, install/start models or alter production data.

On the permanent host only, after normal local provider setup and independent model acceptance, set process environment explicitly before starting the API:

```powershell
$env:JARVIS_MODEL_EXECUTION_MODE='local_only'
$env:JARVIS_MODEL_OLLAMA_ENABLED='true'
$env:JARVIS_MODEL_OLLAMA_BASE_URL='http://127.0.0.1:11434'
$env:JARVIS_MODEL_OLLAMA_MODEL='EXACT_INSTALLED_PLANNING_MODEL'
$env:JARVIS_MODEL_OLLAMA_CAPABILITIES='chat,text_generation,code_generation,structured_output'
$env:JARVIS_DEVELOPMENT_ENABLED='true'
$env:JARVIS_DEVELOPMENT_PROVIDER='local'
$env:JARVIS_DEVELOPMENT_MODEL='EXACT_INSTALLED_CODING_MODEL'
$env:JARVIS_DEVELOPMENT_LOCAL_PROVIDER='ollama'
```

These are owner choices, not model-granted authority. The development model may differ from the planning model; the original planning router is not changed. A separate single-provider development router permits one physical attempt and no fallback. Tool-calling capability is removed from this route. The chosen native adapter must already declare code generation and structured output; merely setting development flags does not register a provider or infer capabilities.

Prepare the existing bounded development workspace and native task/runtime/worker/lease authority. Use existing `/admission/preview` and independently authenticated `/admission/approve` flows, supplying `DevelopmentLimits` with the exact configured origin:

```json
{
  "provider": "ollama",
  "model": "EXACT_INSTALLED_CODING_MODEL",
  "local_endpoint": "http://127.0.0.1:11434",
  "completion_criteria": ["Describe an observable source correction and its required validation."]
}
```

This is the limits portion of the existing admission request; workspace/context/authority and exact preview hash remain required. `local_endpoint` permits only a credential-free loopback HTTP(S) origin with valid port and no path/query/fragment. It is part of the approved plan hash. Changing the endpoint, selected model/provider, activation, retry bounds, capability boundary or trusted policy invalidates use of the old authority. Old serialized limits omit the optional endpoint when absent; they do not acquire local execution permission.

After independent admission, call the existing protected `iterations` endpoint. It passes only application-built bounded context and the fixed proposal schema to the selected installed model. Generated text remains untrusted data. Complete normalized usage, exact provider/model/task/correlation, stopped output, no tool calls and a valid source proposal are required by the existing dispatch service. Failed/uncertain physical calls consume their existing reservation and are not blindly repeated. Reusing a committed command recovers durable response/plan evidence instead of repeating inference.

A successful proposal returns `awaiting_source_approval`. It does not alter files. Inspect the native source plan, independently approve its exact hash using the existing source-review service and apply only with the live bounded workspace/lease. A foreign approval is rejected. Native source history, verification/handoff and original task budget remain authoritative; source-plan text cannot authorize its own mutation. Validation and publication stay false until their independent existing requirements are met.

## Evidence boundary

Tests use a real loopback HTTP server implementing controlled Ollama responses, real migrated temporary SQLite, native disposable Git workspace/source services and independent native approvals. They demonstrate proposal persistence, no mutation before approval, real approved source-byte changes, replay without another model call, and nonpassing validation/publication handoff. Fast controls cover no factory network/client construction, disabled/hosted/remote/unsupported providers, one physical attempt, endpoint validation and stale live configuration.

These tests do not establish real model coding quality, secure candidate-code execution, sandbox containment, live GitHub publication or independent production operation. Complete permanent-host model/isolation acceptance separately. No real Ollama model was installed, downloaded, started or benchmarked on the development computer.

## Cloud forwarding and last-moment authority

Known `-cloud`/`:cloud` model identifiers (case-insensitive) are refused during construction and live activation checks. Model names and a loopback URL alone cannot prove that the daemon has no remote forwarding or renamed cloud aliases. Permanent-host acceptance must verify Ollama's own cloud features are disabled before this path is activated. The owner can use Ollama's documented `OLLAMA_NO_CLOUD=1` or `disable_ollama_cloud` configuration on that host; this application does not alter/start/restart Ollama or treat an application flag/model response as proof of daemon isolation. See [Ollama's configuration documentation](https://github.com/ollama/ollama/blob/main/docs/faq.mdx).

Immediately before physical inference, the router callback rechecks the original native admission/lease/runtime/emergency-stop authority and current provider/model/endpoint/activation. A revocation during asynchronous health/discovery requests prevents the subsequent inference POST. Health reads do not grant execution authority. Complete provider response validation and exact independent source approval remain required afterward.

The shared native Ollama adapter also denies known cloud suffixes before client construction in local_only and free_only modes. This protects ordinary native adapter calls as well as development proposals; it does not prove the absence of renamed remote aliases.
