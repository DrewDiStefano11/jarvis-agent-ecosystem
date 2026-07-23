# 03 - Local Model Provider Contract

This document defines an implementation-neutral interface for local model providers. It decouples the worker logic from any specific LLM or serving framework, enabling seamless switching between testing fakes, OpenAI-compatible local endpoints, Ollama, and LM Studio.

## Provider Abstraction Requirements

The application relies on a generalized `ModelProvider` protocol. The interface must handle:
* **Configuration:** Base URL, model name, optional API keys (if applicable), and provider-specific metadata.
* **Network Tuning:** Connect, read, and request timeouts.
* **Constraints:** Maximum response tokens, maximum context size, temperature, and seed support.
* **Structured Output:** Ensuring models adhere to JSON schema expectations.
* **Observability:** Token accounting (if supported) and health checking.
* **Execution:** Graceful cancellation, retryable error handling, and prompt minimization.

Phase 2B initially prioritizes non-streaming behavior for structured JSON plans.

## Conceptual Provider Interface

```python
from typing import Protocol

class ModelProvider(Protocol):
    async def generate_structured(self, request: ModelRequest) -> ModelResponse:
        """Sends a prompt to the model and expects a validated JSON structure."""
        ...

    async def check_health(self) -> ProviderHealth:
        """Verifies if the model server is available and the requested model is loaded."""
        ...

    async def discover_capabilities(self) -> ProviderCapabilities:
        """Determines context size, seed support, and structured output support."""
        ...
```

## Conceptual Request and Response Contracts

### Model Request
```json
{
  "model": "llama3.1-8b-instruct",
  "messages": [
    {
      "role": "system",
      "content": "You are a helpful planner. Output valid JSON matching the schema."
    },
    {
      "role": "user",
      "content": "Analyze the codebase and list dependencies."
    }
  ],
  "temperature": 0.1,
  "seed": 42,
  "max_tokens": 1024,
  "response_format": {
    "type": "json_schema",
    "json_schema": {
      "name": "plan_schema",
      "schema": {
        "type": "object",
        "properties": {
          "steps": {
            "type": "array",
            "items": {"type": "string"}
          }
        },
        "required": ["steps"]
      }
    }
  }
}
```

### Model Response
```json
{
  "status": "success",
  "content": {
    "steps": [
      "Read package.json",
      "Read requirements.txt"
    ]
  },
  "usage": {
    "prompt_tokens": 45,
    "completion_tokens": 12,
    "total_tokens": 57
  },
  "finish_reason": "stop"
}
```

## Supported Implementations

1. **OpenAI-Compatible HTTP Interface:** The default implementation supporting `v1/chat/completions`. Used for standard local inference engines (vLLM, LM Studio).
2. **Ollama Integration:** An implementation utilizing Ollama's specific REST API, primarily translating schemas into Ollama's `format` parameter.
3. **Deterministic Fake Provider:** Used exclusively for testing. It returns pre-programmed JSON responses based on prompt keywords, ignoring actual model inference to ensure fast, deterministic tests without requiring a GPU.

## Safety and Limitations

* **Prompt Injection:** Context provided to the model (like read file contents) must be clearly separated using delimiters (e.g., `<file_content>...</file_content>`) to resist prompt injection attempts.
* **Sensitive Data Handling:** The provider interface strips internal database IDs or credentials before sending payloads to the model server.
* **Context Minimization:** The system tracks token sizes. If a file exceeds the context window, the provider adapter raises a specific error rather than silently truncating the prompt and corrupting the JSON instruction.
* **Error Classification:**
  * *Retryable:* Network timeouts, `502 Bad Gateway`, or HTTP `429` (too many requests) from the local server.
  * *Non-retryable:* Context length exceeded, schema validation failures (after max internal retries), or invalid model names.
