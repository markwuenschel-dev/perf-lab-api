# LiteLLM gateway

This API does not yet call LLMs on the main request path. When it does, use the
local **litellm-langfuse-gateway** with a **product-specific virtual key** and
origin metadata.

## Env (local `.env`, gitignored)

```env
LITELLM_BASE_URL=http://localhost:4000/v1
LITELLM_VIRTUAL_KEY=sk-...   # never the master key
LITELLM_MODEL=llm-general
SERVICE_NAME=perf-lab-api
ENVIRONMENT=development
```

Provision (from the gateway repo, master key only in operator shell):

```powershell
uv run llg keys create `
  --models llm-general `
  --max-budget 50 `
  --rpm 120 `
  --key-alias perf-lab-api-dev
```

## Call shape (preferred)

Python apps should use `llm_client.GatewayClient` from the gateway repo (path
dependency or package install). `chat()` always attaches `RequestMetadata` from
`SERVICE_NAME` / env when you omit metadata.

```python
from llm_client import GatewayClient, GatewayConfig

with GatewayClient(GatewayConfig.from_env()) as client:
    result = client.chat(
        model="llm-general",
        messages=[{"role": "user", "content": "…"}],
    )
```

Raw OpenAI SDK must pass `extra_body={"metadata": {…}}` (see gateway
`docs/llm-platform/call-attribution.md`).

## Rules

| Do | Don't |
| --- | --- |
| One virtual key per service × environment | Share keys across products |
| Set `SERVICE_NAME=perf-lab-api` | Leave metadata blank |
| Prefer GatewayClient | Put `OPENAI_API_KEY` / master key in this service for chat |

Gateway repo: sibling `litellm-langfuse-gateway` (Compose stack + Langfuse Cloud).
